#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { acquireTargetLock, atomicJson, commandExecutor, portsFromConfig, productVolumes,
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
    log(`Target: ${target.project} on ${target.context}\nCheckout: ${target.root}`);
    log(`Verified deployment: ${previous?.revision ?? "not yet recorded"}`);
    const remoteMain = await run("git", ["ls-remote", "https://github.com/aaweaver-actuary/tempo", "refs/heads/main"], { allowFailure: true, timeout: 15_000 });
    const newestRevision = remoteMain.stdout.trim().split(/\s+/)[0];
    log(remoteMain.code === 0 && newestRevision ? `Latest main: ${newestRevision}${newestRevision === previous?.revision ? " (deployed)" : " (not recorded as deployed)"}` : "Update availability: GitHub could not be reached.");
    log(`Required local schema: ${schemaVersionFromSource(readFileSync(join(target.root, "backend/app/schema_version.py"), "utf8"))}`);
    const running = await runtime.runningServices(); log(`Running services: ${running.join(", ") || "none"}`);
    if (running.includes("postgres")) {
      const config = runtime.configuration;
      const result = await runtime.compose(["exec", "-T", "postgres", "psql", "-X", "-tA", "-U",
        config.services.postgres.environment.POSTGRES_USER ?? "postgres", "-d", config.services.postgres.environment.POSTGRES_DB,
        "-c", "SELECT version FROM tempo_schema_migrations ORDER BY version"], { allowFailure: true });
      log(result.code === 0 ? `Applied schema versions: ${result.stdout.trim().split("\n").join(", ")}` : "Database schema could not be read; inspect PostgreSQL logs.");
    }
    const operation = readJson(join(stateDirectory, "operation.json"));
    if (operation) log(`Last operation: ${operation.phase}${operation.failure ? ` — ${operation.failure}` : ""}`);
    if (options.command === "logs") {
      const service = options.services[0];
      if (service && !runtime.configuration.services[service]) throw new Error(`Unknown service: ${service}`);
      await runtime.compose(["logs", "--no-color", "--tail", "100", ...(options.flags.has("--follow") ? ["--follow"] : []), ...(service ? [service] : [])], { echo: true });
    } else if (options.flags.has("--plan")) {
      log("Planned actions: verify current main CI; preserve local changes; prepare coherent images; check initialized storage and schema; if needed stop writers, verify a backup restore, and migrate; start services and verify readiness. No source, image, service, or database changes were made.");
    } else if (options.command === "doctor") {
      const response = await fetch(`${target.webUrl}/api/health`, { signal: AbortSignal.timeout(5000) }).catch(() => null);
      log(response ? `API health: HTTP ${response.status} ${redact(await response.text(), secrets)}` : "API health: unavailable. Run tempo start; it will check updates and report any blocker.");
    }
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
      if (!previous || options.command === "migrate") throw new Error("No eligible update can be applied. Run tempo doctor and resolve the blocker.");
      log(`Starting previously verified revision ${previous.revision}; the update has not been applied.`);
    }
    const operation = readJson(join(stateDirectory, "operation.json"));
    const selectedRevision = candidate?.revision ?? previous.revision;
    if (operation && operation.revision === selectedRevision && !options.flags.has("--retry")
      && (operation.phase === "applying_migrations" || (operation.phase === "failed" && operation.failed_phase === "applying_migrations")))
      throw new Error("Previous migration failed or was interrupted. Inspect tempo doctor and the recorded logs, fix the cause, then explicitly run tempo migrate --retry.");
    runtime = createRuntime(target, { run, stateDirectory, previous, revision: selectedRevision,
      evidence: candidate?.evidence, fallback: !candidate, log });
    await runtime.inspectTarget(); secrets.push(...runtime.secretValues);
    const recreate = options.command === "restart" || Boolean(candidate && previous?.revision !== selectedRevision);
    await executeLifecycle({ recreate }, { ...runtime, ensureImages: async () => {
      await runtime.ensureImages();
      if (candidate && ((await run("git", ["rev-parse", "HEAD"])).stdout.trim() !== selectedRevision
        || (await run("git", ["status", "--porcelain"])).stdout.trim()))
        throw new Error("Checkout changed while images were prepared. Local work is preserved; no database maintenance or application shutdown was started.");
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
