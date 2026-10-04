#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { acquireTargetLock, assessMainVerification, atomicJson, commandExecutor, portsFromConfig, productVolumes,
  redact, schemaVersionFromSource, selectCandidate, targetKey, validateTarget } from "./tempo-deployment.mjs";
import { createRuntime, executeLifecycle } from "./tempo-runtime.mjs";

const help = `Tempo — manage the existing Docker/PostgreSQL installation

  tempo install              Install the command and register this checkout once
  tempo start                Update, check, migrate when needed, and start in the background
  tempo restart              Run the same checks and recreate application services
  tempo stop                 Stop services without removing data
  tempo status               Show the deployed revision, schema, and service state
  tempo doctor               Read-only target and readiness diagnosis
  tempo logs [service]       Show recent logs (add --follow to follow)
  tempo backup               Create and restore-verify a backup of the deployed version
  tempo migrate              Run the checked update/migration workflow without opening a browser

Options: --plan previews maintenance without changing services or source;
         --no-open avoids opening the browser; --retry explicitly retries interrupted
         or failed maintenance after inspection; --config FILE selects a registration.

After a merge, the normal command is: tempo start
`;

export function parseArguments(argumentsList) {
  const args = [...argumentsList];
  let configPath = process.env.TEMPO_CLI_CONFIG ?? join(homedir(), ".config", "tempo", "config.json");
  const configIndex = args.indexOf("--config");
  if (configIndex >= 0) {
    if (!args[configIndex + 1] || args[configIndex + 1].startsWith("--")) throw new Error("--config needs a file path.");
    configPath = resolve(args[configIndex + 1]); args.splice(configIndex, 2);
  }
  const command = args.shift() ?? "help";
  const flags = new Set(args.filter(argument => argument.startsWith("--")));
  const services = args.filter(argument => !argument.startsWith("--"));
  if (["--help", "-h", "help"].includes(command)) return { command: "help", configPath, flags, services };
  if (!["install", "start", "restart", "stop", "status", "doctor", "logs", "backup", "migrate"].includes(command))
    throw new Error(`Unknown Tempo command: ${command}. Run tempo help.`);
  if ([...flags].some(flag => !["--plan", "--no-open", "--follow", "--retry"].includes(flag))
    || (command !== "logs" && services.length) || services.length > 1
    || (flags.has("--follow") && command !== "logs")) throw new Error("Invalid options. Run tempo help.");
  return { command, configPath, flags, services };
}

function readJson(path) { return existsSync(path) ? JSON.parse(readFileSync(path, "utf8")) : null; }

async function install(options, sourceRoot, log) {
  if (sourceRoot.split(/[\\/]/).includes(".dev-copies"))
    throw new Error("Install from the main study checkout after the CLI PR is merged, not an isolated development copy.");
  if (existsSync(options.configPath)) throw new Error("Tempo is already registered. Run tempo doctor; registration will not be overwritten.");
  const run = commandExecutor({ root: sourceRoot, output: log });
  await run("git", ["--version"]);
  const context = (await run("docker", ["context", "show"])).stdout.trim();
  await run("docker", ["--context", context, "info"]);
  const daemonId = (await run("docker", ["--context", context, "info", "--format", "{{.ID}}"])).stdout.trim();
  const envFile = join(sourceRoot, ".env");
  if (!existsSync(envFile)) throw new Error("Existing installation needs its .env file; see docs/POSTGRES-MAINTENANCE.md.");
  const composeFiles = [join(sourceRoot, "docker-compose.yml"), join(sourceRoot, "docker-compose.postgres-maintenance.yml")];
  const config = JSON.parse((await run("docker", ["--context", context, "compose", "--project-directory", sourceRoot,
    "--env-file", envFile, "-p", "tempo", ...composeFiles.flatMap(path => ["-f", path]), "--profile", "maintenance", "config", "--format", "json"])).stdout);
  const target = { version: 1, root: sourceRoot, context, daemonId, project: "tempo", envFile, composeFiles,
    volumes: productVolumes, ports: portsFromConfig(config), postgresVolumeKey: "tempo-postgres-data",
    postgresMajor: 18, postgresImage: config.services.postgres.image, webUrl: "http://127.0.0.1:3000" };
  if (target.ports.some(port => port.host !== "127.0.0.1") || !target.ports.some(port => port.service === "web" && port.port === "3000"))
    throw new Error("Existing installation must expose the standard Tempo web endpoint on loopback port 3000.");
  validateTarget(config, target);
  for (const volume of Object.values(target.volumes)) await run("docker", ["--context", context, "volume", "inspect", volume.name]);
  if (options.flags.has("--plan")) { log(`Would register ${sourceRoot}, context ${context}, project tempo, and install ~/.local/bin/tempo.`); return; }
  const binDirectory = join(homedir(), ".local", "bin");
  const launcher = join(binDirectory, "tempo");
  if (existsSync(launcher)) throw new Error("~/.local/bin/tempo already exists; it will not be overwritten.");
  mkdirSync(binDirectory, { recursive: true, mode: 0o755 });
  // A quoted exec keeps spaces and punctuation in the checkout path literal.
  const quotedPath = `'${join(sourceRoot, "tempo").replaceAll("'", "'\\''")}'`;
  writeFileSync(launcher, `#!/bin/sh\nexec ${quotedPath} "$@"\n`, { mode: 0o755, flag: "wx" });
  const shellProfile = join(homedir(), ".zshrc");
  const profile = existsSync(shellProfile) ? readFileSync(shellProfile, "utf8") : "";
  if (!profile.includes("# Tempo CLI path")) writeFileSync(shellProfile, `${profile}\n# Tempo CLI path\nexport PATH="$HOME/.local/bin:$PATH"\n`, { mode: 0o600 });
  atomicJson(options.configPath, target);
  log("Tempo CLI installed. Open a new terminal, then run tempo start. In this terminal, ./tempo start also works.");
}

async function ensureDocker(target, run, allowLaunch, log) {
  const args = ["--context", target.context, "info"];
  if ((await run("docker", args, { allowFailure: true, timeout: 5000 })).code === 0) return;
  if (!allowLaunch || process.platform !== "darwin") throw new Error("Docker is unavailable. Start Docker Desktop, then run tempo start.");
  log("Starting Docker Desktop…"); await run("open", ["-a", "Docker"]);
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if ((await run("docker", args, { allowFailure: true, timeout: 5000 })).code === 0) return;
    await new Promise(resolveWait => setTimeout(resolveWait, 1000));
  }
  throw new Error("Docker Desktop did not become ready within 120 seconds.");
}

async function reportDiagnostics({ options, target, run, runtime, previous, stateDirectory, secrets, log }) {
  log(`Target: ${target.project} on ${target.context}\nCheckout: ${target.root}`);
  if (options.command === "logs") {
    log(`Verified deployment: ${previous?.revision ?? "not yet recorded"}`);
    const service = options.services[0];
    if (service && !runtime.configuration.services[service]) throw new Error(`Unknown service: ${service}`);
    await runtime.compose(["logs", "--no-color", "--tail", "100", ...(options.flags.has("--follow") ? ["--follow"] : []), ...(service ? [service] : [])], { echo: true });
    return;
  }
  const gitRead = async args => run("git", args, { allowFailure: true, timeout: 5000 });
  const branch = (await gitRead(["branch", "--show-current"])).stdout.trim();
  const head = (await gitRead(["rev-parse", "HEAD"])).stdout.trim();
  const sourceStatus = await gitRead(["--no-optional-locks", "status", "--porcelain"]);
  const origin = (await gitRead(["remote", "get-url", "origin"])).stdout.trim();
  log(`Local checkout: ${branch || "unknown branch"} at ${head || "unknown revision"}; ${sourceStatus.code !== 0 ? "cleanliness unknown" : sourceStatus.stdout.trim() ? "local changes present" : "clean"}`);
  log(`Verified deployment: ${previous?.revision ?? "not yet recorded"}`);
  const requiredSchema = schemaVersionFromSource(readFileSync(join(target.root, "backend/app/schema_version.py"), "utf8"));
  log(`Required local schema: ${requiredSchema}`);
  const running = await runtime.runningServices(); log(`Running services: ${running.join(", ") || "none"}`);
  const identity = await runtime.inspectRunningRevision();
  log(`Running application revision: ${identity.status} — ${identity.detail}`);
  log(`Receipt consistency: ${identity.receipt}`);
  if (running.includes("postgres")) {
    const config = runtime.configuration;
    const result = await runtime.compose(["exec", "-T", "-e",
      "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=100", "postgres", "psql", "-X", "-tA", "-v", "ON_ERROR_STOP=1", "-U",
      config.services.postgres.environment.POSTGRES_USER ?? "postgres", "-d", config.services.postgres.environment.POSTGRES_DB,
      "-c", "SELECT version FROM tempo_schema_migrations ORDER BY version"], { allowFailure: true, timeout: 5000 });
    const applied = result.stdout.trim() ? result.stdout.trim().split(/\s+/).map(Number) : [];
    if (result.code === 0 && applied.every(version => Number.isInteger(version) && version > 0)) {
      log(`Applied schema versions: ${applied.join(", ") || "none"}`);
      const newestApplied = Math.max(0, ...applied);
      const gaps = Array.from({ length: newestApplied }, (_, index) => index + 1).filter(version => !applied.includes(version));
      const pending = Array.from({ length: Math.max(0, requiredSchema - newestApplied) }, (_, index) => newestApplied + index + 1);
      log(`Migration ledger gaps: ${gaps.join(", ") || "none"}`);
      log(`Pending local migrations: ${pending.join(", ") || "none"}`);
      if (newestApplied > requiredSchema) log(`Database schema ${newestApplied} is ahead of local source ${requiredSchema}; inspect compatible source before updating.`);
    } else log("Database schema could not be read; inspect PostgreSQL logs.");
  } else log("Applied schema versions: unavailable (PostgreSQL is not running)");
  const operation = readJson(join(stateDirectory, "operation.json"));
  if (operation) log(`Last operation: ${operation.phase}${operation.failure ? ` — ${redact(operation.failure, secrets)}` : ""}`);
  const guard = readJson(join(stateDirectory, "migration-guard.json"));
  if (guard) log(`Original migration verification: ${guard.state}; origin ${guard.origin_revision}; backup ${guard.backup?.filename ?? "unknown"}`);
  log("Background completion: unverified; service state and API readiness do not prove completion of all background work.");

  // The entire diagnostic remote lookup is bounded; it never fetches Git objects.
  const remoteSignal = AbortSignal.timeout(30_000);
  let remoteRevision, verification;
  try {
    const remoteMain = await run("git", ["ls-remote", "https://github.com/aaweaver-actuary/tempo", "refs/heads/main"], { allowFailure: true, timeout: 15_000 });
    remoteRevision = remoteMain.stdout.trim().split(/\s+/)[0];
    if (remoteMain.code !== 0 || !/^[a-f0-9]{40}$/.test(remoteRevision ?? "")) throw new Error("Remote main could not be read.");
    log(`Latest main: ${remoteRevision}${remoteRevision === previous?.revision ? " (matches recorded receipt)" : " (not recorded as deployed)"}`);
    verification = await assessMainVerification(remoteRevision, undefined, { signal: remoteSignal });
  } catch (error) { verification = { status: "unavailable", message: error.message }; }
  log(`Verification: ${verification.status} — ${redact(verification.message, secrets)}`);
  let sourceBlocker;
  if (branch !== "main") sourceBlocker = "registered checkout must be on main";
  else if (sourceStatus.code !== 0) sourceBlocker = "checkout cleanliness could not be verified";
  else if (sourceStatus.stdout.trim()) sourceBlocker = "local changes are preserved; save them on a separate branch before updating";
  else if (!/^(?:https?:\/\/github\.com\/|git@github\.com:)aaweaver-actuary\/tempo(?:\.git)?$/.test(origin)) sourceBlocker = "registered checkout has an unexpected GitHub remote";
  let ancestry = "unknown";
  if (head && remoteRevision) {
    if (head === remoteRevision) ancestry = "compatible";
    else if ((await gitRead(["cat-file", "-e", `${remoteRevision}^{commit}`])).code === 0) {
      const result = await gitRead(["merge-base", "--is-ancestor", head, remoteRevision]);
      ancestry = result.code === 0 ? "compatible" : result.code === 1 ? "diverged" : "unknown";
    }
  }
  log(`Local ancestry: ${ancestry}${ancestry === "unknown" ? "; no Git objects were fetched" : ""}`);
  if (!sourceBlocker && ancestry === "diverged") sourceBlocker = "local main has diverged; Tempo will not reset or merge your work";
  if (!sourceBlocker && guard?.state === "pending") sourceBlocker = "original migration verification requires inspection and explicit tempo migrate --retry";
  log(`Update eligibility: ${sourceBlocker ? `blocked — ${sourceBlocker}` : verification.status !== "verified" ? verification.status
    : ancestry === "unknown" ? "unknown — ancestry must be checked by the updater" : "eligible (deployment readiness still checked by tempo start)"}`);
  if (verification.status !== "verified") {
    if (!previous) log("No previous verified deployment is recorded, so the CLI has no verified fallback. This diagnostic has not applied the update.");
    else log(`Recorded fallback: ${previous.revision}; images, schema and readiness must still pass the normal updater. This diagnostic has not applied the update.`);
    if (verification.status === "pending") log(`Inspect ${verification.run?.html_url ?? "the exact-revision workflow"}, then run tempo start once eligible.`);
    else if (verification.status === "failed") log(`Inspect and repair required job ${verification.job} in ${verification.run?.html_url}; run tempo start after exact-revision verification passes.`);
    else if (verification.status === "missing") log(`Inspect allowed main verification for ${remoteRevision ?? "the current revision"}; run tempo start after complete exact-revision evidence exists.`);
    else log("Restore GitHub access and inspect authentication, rate limits or network errors, then rerun tempo doctor. Unavailable evidence is not a failed test.");
  }
  if (guard?.state === "pending") log("Original migration verification remains required. Preserve its backup and guard, inspect the cause, then use tempo migrate --retry explicitly.");
  if (options.flags.has("--plan")) {
    log("Planned actions once eligible: recheck current main CI and source; prepare coherent images; check initialized storage and schema; if needed stop writers, verify a backup restore, and migrate; start services and verify readiness. No source, image, service, or database changes were made.");
  } else if (options.command === "doctor") {
    try {
      const response = await fetch(`${target.webUrl}/api/health`, { signal: AbortSignal.timeout(5000) });
      log(`API health: HTTP ${response.status} ${redact(await response.text(), secrets)}`);
    } catch { log("API health: unavailable. Inspect API logs and the update eligibility above."); }
    log("API readiness describes the running API's schema and basic worker/queue checks; it does not prove all background work has completed.");
  }
}

export async function main(argumentsList = process.argv.slice(2), log = console.log) {
  const options = parseArguments(argumentsList);
  if (options.command === "help") { log(help); return 0; }
  const sourceRoot = realpathSync(fileURLToPath(new URL("../", import.meta.url)));
  const [nodeMajor, nodeMinor] = process.versions.node.split(".").map(Number);
  if (nodeMajor < 22 || (nodeMajor === 22 && nodeMinor < 13)) throw new Error("Tempo CLI requires Node.js 22.13 or newer.");
  if (options.command === "install") { await install(options, sourceRoot, log); return 0; }
  const target = readJson(options.configPath);
  if (!target || target.version !== 1) throw new Error("Tempo is not registered. Run ./tempo install from the main checkout once.");
  if (process.env.TEMPO_UPGRADE_EXPECTED_PROJECT && process.env.TEMPO_UPGRADE_EXPECTED_PROJECT !== target.project)
    throw new Error("Registered target differs from TEMPO_UPGRADE_EXPECTED_PROJECT; maintenance was not started.");
  if (!existsSync(target.root) || !target.context || !target.daemonId || !target.project || !target.composeFiles?.length || !target.postgresVolumeKey)
    throw new Error("Tempo registration is incomplete; inspect the config file before proceeding.");
  const secrets = Object.entries(process.env).filter(([key]) => /TOKEN|PASSWORD|SECRET/i.test(key)).map(([, value]) => value);
  const run = commandExecutor({ root: target.root, output: log, secretValues: secrets });
  const readOnly = ["status", "doctor", "logs"].includes(options.command) || options.flags.has("--plan");
  await ensureDocker(target, run, !readOnly && ["start", "restart", "migrate"].includes(options.command), log);
  const stateRoot = process.env.TEMPO_CLI_STATE_DIR ?? join(homedir(), ".local", "share", "tempo");
  const stateDirectory = join(stateRoot, targetKey(target));
  const previous = readJson(join(stateDirectory, "deployment.json"));
  let runtime = createRuntime(target, { run, stateDirectory, previous, revision: previous?.revision ?? "unrecorded", fallback: Boolean(previous), log });
  await runtime.inspectTarget(); secrets.push(...runtime.secretValues);

  if (readOnly) {
    await reportDiagnostics({ options, target, run, runtime, previous, stateDirectory, secrets, log });
    return 0;
  }

  const release = acquireTargetLock(stateDirectory);
  try {
    if (options.command === "stop") { await runtime.stopAll(); log("Tempo stopped. Study data is retained."); return 0; }
    if (options.command === "backup") {
      if (!previous) throw new Error("Run tempo start once to record a verified deployment before using tempo backup.");
      await runtime.backupOnly(); return 0;
    }
    let candidate, blockedUpdate;
    try { candidate = await selectCandidate(target, run); }
    catch (error) { blockedUpdate = redact(error.message, secrets); }
    if (candidate?.updated) {
      // Release before re-execution; the updated CLI reacquires the target lock
      // and revalidates source/CI/target before doing any deployment work.
      release();
      return (await run(process.execPath, [join(target.root, "scripts/tempo-cli.mjs"), ...argumentsList], { allowFailure: true, echo: true })).code ?? 1;
    }
    if (blockedUpdate) {
      log(`Update blocked: ${blockedUpdate}`);
      if (!previous) throw new Error("No eligible update can be applied. No previous verified deployment is recorded, so the CLI has no verified fallback. This blocked attempt has not applied the update. Run tempo doctor, resolve the reported blocker, then run tempo start once eligible.");
      if (options.command === "migrate") throw new Error("No eligible update can be applied. Run tempo doctor and resolve the blocker.");
      log(`Starting previously verified revision ${previous.revision}; the update has not been applied.`);
    }
    const selectedRevision = candidate?.revision ?? previous.revision;
    runtime = createRuntime(target, { run, stateDirectory, previous, revision: selectedRevision,
      evidence: candidate?.evidence, fallback: !candidate, retry: options.flags.has("--retry"), log });
    await runtime.inspectTarget(); secrets.push(...runtime.secretValues);
    const recreate = options.command === "restart" || Boolean(candidate && previous?.revision !== selectedRevision);
    await executeLifecycle({ recreate }, { ...runtime, ensureImages: async () => {
      const imagePreparation = await runtime.ensureImages();
      if (candidate && ((await run("git", ["rev-parse", "HEAD"])).stdout.trim() !== selectedRevision
        || (await run("git", ["status", "--porcelain"])).stdout.trim()))
        throw new Error("Checkout changed while images were prepared. Local work is preserved; no database maintenance or application shutdown was started.");
      return imagePreparation;
    } });
    log(`Tempo ready at ${target.webUrl} (revision ${selectedRevision.slice(0, 12)})${blockedUpdate ? "; update remains blocked" : ""}.`);
    if (options.command !== "migrate" && !options.flags.has("--no-open") && process.platform === "darwin")
      await run("open", [target.webUrl]);
    return 0;
  } finally { release(); }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await main(); }
  catch (error) { console.error(`Tempo: ${redact(error.message, Object.entries(process.env).filter(([key]) => /TOKEN|PASSWORD|SECRET/i.test(key)).map(([, value]) => value))}`); process.exitCode = 1; }
}
