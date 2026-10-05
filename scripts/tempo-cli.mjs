#!/usr/bin/env node
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { TempoProblem, classifyRedisReply, phaseGuidance, assertInstallationUnchanged, inspectMaintenance, installationFingerprint, recoveryAssessment, verificationProblem, waitForVerification } from "./tempo-guidance.mjs";
import { acquireTargetLock, assessCandidate, assessMainVerification, atomicJson, commandExecutor, inspectCandidateSource, portsFromConfig, productVolumes,
  redact, schemaVersionFromSource, selectCandidate, targetKey, validateTarget } from "./tempo-deployment.mjs";
import { assessMigrationRecovery, createRuntime, executeLifecycle } from "./tempo-runtime.mjs";

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
         --verbose shows technical evidence and underlying command output;
         --no-wait assesses release checks once without waiting;
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
  if ([...flags].some(flag => !["--plan", "--no-open", "--follow", "--retry", "--verbose", "--no-wait"].includes(flag))
    || (command !== "logs" && services.length) || services.length > 1
    || (flags.has("--follow") && command !== "logs")
    || (flags.has("--no-wait") && !["start", "restart", "migrate"].includes(command))) throw new Error("Invalid options. Run tempo help.");
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
  if (!allowLaunch || process.platform !== "darwin") throw new TempoProblem("docker_unavailable", "Docker is unavailable.", { action: "Next: start Docker Desktop, then run tempo start." });
  log("Starting Docker Desktop…"); await run("open", ["-a", "Docker"]);
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if ((await run("docker", args, { allowFailure: true, timeout: 5000 })).code === 0) return;
    await new Promise(resolveWait => setTimeout(resolveWait, 1000));
  }
  throw new Error("Docker Desktop did not become ready within 120 seconds.");
}

async function reportDiagnostics({ options, target, run, runtime, previous, stateDirectory, secrets, log }) {
  if (options.command === "logs") {
    const service = options.services[0];
    if (service && !runtime.configuration.services[service]) throw new Error(`Unknown service: ${service}`);
    await runtime.compose(["logs", "--no-color", "--tail", "100", ...(options.flags.has("--follow") ? ["--follow"] : []), ...(service ? [service] : [])], { echo: true });
    return;
  }
  const evidence = [];
  const detail = message => evidence.push(message);
  detail(`Target: ${target.project} on ${target.context}\nCheckout: ${target.root}`);
  const source = await inspectCandidateSource(target, run);
  let sourceProblem = source.problem;
  detail(`Local checkout: ${source.branch || "unknown branch"} at ${source.head || "unknown revision"}; ${source.changes ? "local changes present" : "clean"}`);
  detail(`Verified deployment: ${previous?.revision ?? "not yet recorded"}`);
  const requiredSchema = schemaVersionFromSource(readFileSync(join(target.root, "backend/app/schema_version.py"), "utf8"));
  detail(`Required local schema: ${requiredSchema}`);
  const running = await runtime.runningServices();
  detail(`Running services: ${running.join(", ") || "none"}`);
  const identity = await runtime.inspectRunningRevision();
  detail(`Running application revision: ${identity.status} — ${identity.detail}`);
  detail(`Receipt consistency: ${identity.receipt}`);
  let pending = [], ledgerProblem;
  if (running.includes("postgres")) {
    const result = await runtime.compose(["exec", "-T", "-e",
      "PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=100", "postgres", "psql", "-X", "-tA", "-v", "ON_ERROR_STOP=1", "-U",
      runtime.configuration.services.postgres.environment.POSTGRES_USER ?? "postgres", "-d", runtime.configuration.services.postgres.environment.POSTGRES_DB,
      "-c", "SELECT version FROM tempo_schema_migrations ORDER BY version"], { allowFailure: true, timeout: 5000 });
    const applied = result.stdout.trim() ? result.stdout.trim().split(/\s+/).map(Number) : [];
    if (result.code === 0 && applied.every(version => Number.isInteger(version) && version > 0)) {
      const newest = Math.max(0, ...applied);
      const gaps = Array.from({ length: newest }, (_, index) => index + 1).filter(version => !applied.includes(version));
      pending = Array.from({ length: Math.max(0, requiredSchema - newest) }, (_, index) => newest + index + 1);
      detail(`Applied schema versions: ${applied.join(", ") || "none"}`);
      detail(`Migration ledger gaps: ${gaps.join(", ") || "none"}`);
      detail(`Pending local migrations: ${pending.join(", ") || "none"}`);
      if (gaps.length) ledgerProblem = `The migration ledger has missing versions: ${gaps.join(", ")}.`;
      if (newest > requiredSchema) ledgerProblem = `Database schema ${newest} is ahead of local source ${requiredSchema}; inspect compatible source before updating.`;
      if (ledgerProblem) detail(ledgerProblem);
    } else detail("Database schema could not be read; inspect PostgreSQL logs.");
  } else detail("Applied schema versions: unavailable (PostgreSQL is not running)");
  const operation = readJson(join(stateDirectory, "operation.json"));
  if (operation) {
    detail(`Historical operation: ${operation.phase}; revision ${operation.revision ?? "not recorded"}; phase ${operation.failed_phase ?? "not recorded"}; ${operation.failed_at ? `failed at ${operation.failed_at}` : operation.started_at ? `started at ${operation.started_at}; failure timestamp not recorded` : "timestamp not recorded"}`);
    if (!operation.revision) detail("Historical revision not recorded.");
    if (operation.failure) detail(`Historical failure: ${operation.failure}`);
    if (operation.failure_log) detail(`Failure evidence: ${operation.failure_log}`);
  }
  const guard = readJson(join(stateDirectory, "migration-guard.json"));
  if (guard) detail(`Original migration verification: ${guard.state}; origin ${guard.origin_revision}; backup ${guard.backup?.filename ?? "unknown"}`);
  const migrationRecovery = assessMigrationRecovery({ guard, operation, target, configuration: runtime.configuration,
    retry: options.flags.has("--retry") && ["start", "restart", "migrate"].includes(options.command) });
  if (migrationRecovery.status !== "clear") detail(`Migration recovery: ${migrationRecovery.status} — ${migrationRecovery.message}`);
  if (migrationRecovery.status === "retry-authorized") {
    detail("Explicit migration recovery attempt: permitted and planned; recovery has not been performed by this diagnostic.");
    detail("Ordinary startup safety: not established; original-history verification must succeed in the real lifecycle first.");
  }
  detail("Background completion: unverified; service state and API readiness do not prove completion of all background work.");
  let remoteRevision, verification;
  try {
    const signal = AbortSignal.timeout(30_000);
    const remote = await run("git", ["ls-remote", "https://github.com/aaweaver-actuary/tempo", "refs/heads/main"], { allowFailure: true, timeout: 15_000, signal });
    remoteRevision = remote.stdout.trim().split(/\s+/)[0];
    if (remote.code !== 0 || !/^[a-f0-9]{40}$/.test(remoteRevision ?? "")) throw new Error("Remote main could not be read.");
    detail(`Latest main: ${remoteRevision}${remoteRevision === previous?.revision ? " (matches recorded receipt)" : " (not recorded as deployed)"}`);
    verification = await assessMainVerification(remoteRevision, undefined, { signal });
  } catch (error) { verification = { status: "unavailable", message: error.message }; }
  detail(`Verification: ${verification.status} — ${verification.message}`);
  let ancestry = "unknown";
  if (source.head && remoteRevision) {
    if (source.head === remoteRevision) ancestry = "compatible";
    else if ((await run("git", ["cat-file", "-e", `${remoteRevision}^{commit}`], { allowFailure: true, timeout: 5000 })).code === 0) {
      const result = await run("git", ["merge-base", "--is-ancestor", source.head, remoteRevision], { allowFailure: true, timeout: 5000 });
      ancestry = result.code === 0 ? "compatible" : result.code === 1 ? "diverged" : "unknown";
    }
  }
  if (!sourceProblem && ancestry === "diverged") sourceProblem = new TempoProblem("source_diverged", "Local main has diverged; Tempo will not reset or merge your work.",
    { action: `Next: preserve and reconcile ${target.root} with remote main without discarding local commits.` });
  detail(`Local ancestry: ${ancestry}${ancestry === "unknown" ? "; no Git objects were fetched" : ""}`);
  const maintenance = inspectMaintenance(stateDirectory);
  detail(`Maintenance: ${maintenance.status}${maintenance.pid ? `; owner PID ${maintenance.pid}` : ""}`);
  let assessment = recoveryAssessment({ maintenance, sourceProblem, migrationRecovery, verification, ledgerProblem });

  if (!previous) detail("No previous verified deployment is recorded, so the CLI has no verified fallback. This diagnostic has not applied the update.");
  else detail(`Recorded fallback: ${previous.revision}; images, schema and readiness must still pass the normal updater. This diagnostic has not applied the update.`);
  if (guard?.state === "pending") detail("Original migration verification remains required. Preserve its backup and guard; repair the cause, then use tempo migrate --retry explicitly.");

  let apiResponding = false, redisReady;
  if (options.command === "doctor") {
    try {
      const response = await fetch(`${target.webUrl}/api/health`, { signal: AbortSignal.timeout(5000) });
      const text = await response.text();
      detail(`API health: HTTP ${response.status} ${text}`);
      try { const health = JSON.parse(text); apiResponding = response.ok && health.status === "ok" && health.storage === "postgresql" && Boolean(health.test_instance) === Boolean(target.disposable); } catch { /* An incompatible response does not establish service availability. */ }
    } catch { detail("API health: unavailable."); }
    if (running.includes("redis")) {
      try {
        const result = await runtime.compose(["exec", "-T", "redis", "redis-cli", "-e", "--raw", "ping"], { allowFailure: true, timeout: 5000 });
        const reply = classifyRedisReply(result);
        redisReady = reply.status === "ready";
        if (reply.status === "terminal") assessment = recoveryAssessment({ sourceProblem, migrationRecovery, verification, ledgerProblem,
          maintenance, serviceProblem: new TempoProblem("redis_terminal", `Redis rejected its readiness check: ${reply.reason.slice(0, 400)}`,
            { action: phaseGuidance("checking_redis").action }) });
        detail(`Current Redis: ${redisReady ? "PONG" : result.stdout.trim() || result.stderr.trim() || "probe unavailable"}`);
      } catch { detail("Current Redis: probe unavailable."); }
    }
    detail("API readiness describes the running API's schema and basic worker/queue checks; it does not prove all background work has completed.");
  }
  detail(`Update eligibility: ${assessment.code === "eligible" ? ancestry === "unknown" ? "unknown — ancestry must be checked by the updater" : "eligible (deployment readiness still checked by tempo start)" : assessment.code.startsWith("verification_") ? verification.status : `blocked — ${assessment.message}`}`);
  const emit = message => log(redact(message, secrets));
  if (options.command === "doctor") emit(apiResponding
    ? `The running Tempo service responds${identity.status !== "consistent" || !previous || !identity.receipt.startsWith("matches") ? ", but its version is unverified" : ""}.`
    : "The Tempo study service is not responding with a valid PostgreSQL health result.");
  else emit(`Running services: ${running.length ? "present (health not checked)" : "none"}; deployment ${previous ? "recorded" : "not yet verified"}.`);
  emit(assessment.message);
  emit(assessment.action);
  if (assessment.code === "verification_unavailable") emit(`Cause: ${verification.message}`);
  if (assessment.code === "verification_failed" && verification.run?.html_url) emit(`Release checks: ${verification.run.html_url}`);
  if (operation?.failure?.includes("Redis") && redisReady) emit("The previous Redis error is historical; Redis responds now.");
  if (pending.length && !ledgerProblem && (assessment.code === "eligible" || assessment.code.startsWith("verification_"))) emit(`${pending.length} migration${pending.length === 1 ? " is" : "s are"} pending; Tempo applies them automatically after verifying a backup.`);
  if (options.flags.has("--plan")) emit("Plan only: no source, image, service, or database changes were made. The real command rechecks eligibility and preserves original history before startup.");
  emit("Details: tempo doctor --verbose");
  if (options.flags.has("--verbose")) for (const message of evidence) emit(message);
}

export async function main(argumentsList = process.argv.slice(2), log = console.log, { verificationWaitOptions = {} } = {}) {
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
  const commandEnvironment = { ...process.env };
  const execute = commandExecutor({ root: target.root, environment: commandEnvironment, output: log, secretValues: secrets });
  const run = (command, args, settings = {}) => execute(command, args, { ...settings,
    echo: Boolean(settings.echo && (options.flags.has("--verbose") || options.command === "logs")) });
  const readOnly = ["status", "doctor", "logs"].includes(options.command) || options.flags.has("--plan");
  await ensureDocker(target, run, !readOnly && ["start", "restart", "migrate"].includes(options.command), log);
  const stateRoot = process.env.TEMPO_CLI_STATE_DIR ?? join(homedir(), ".local", "share", "tempo");
  const stateDirectory = join(stateRoot, targetKey(target));
  const previous = readJson(join(stateDirectory, "deployment.json"));
  let runtime = createRuntime(target, { run, stateDirectory, previous, revision: previous?.revision ?? "unrecorded", fallback: Boolean(previous), log });
  try { await runtime.inspectTarget(); }
  catch (error) {
    error.action ??= `Next: correct the named installation/credential conflict in ${options.configPath} or ${target.envFile}. Preserve all study volumes; storage recovery is documented in ${join(target.root, "docs/POSTGRES-MAINTENANCE.md")}.`;
    throw error;
  }
  secrets.push(...runtime.secretValues);

  if (readOnly) {
    await reportDiagnostics({ options, target, run, runtime, previous, stateDirectory, secrets, log });
    return 0;
  }

  if (["stop", "backup"].includes(options.command)) {
    const release = acquireTargetLock(stateDirectory);
    try {
      if (options.command === "stop") { await runtime.stopAll(); log("Tempo stopped. Study data is retained."); return 0; }
      if (!previous) throw new TempoProblem("no_receipt", "A verified deployment must be recorded before tempo backup.", { action: "Run: tempo start" });
      await runtime.backupOnly(); return 0;
    } finally { release(); }
  }

  const continuation = commandEnvironment.TEMPO_CLI_CONTINUATION ? JSON.parse(commandEnvironment.TEMPO_CLI_CONTINUATION) : null;
  delete commandEnvironment.TEMPO_CLI_CONTINUATION;
  if (continuation && (!continuation.installationState || !continuation.source || !Number.isFinite(continuation.deadline)))
    throw new TempoProblem("invalid_continuation", "The update continuation is incomplete.", { action: "Run: tempo start in a new shell without TEMPO_CLI_CONTINUATION." });
  const installationState = continuation?.installationState ?? installationFingerprint(stateDirectory, options.configPath);
  const originalSource = continuation?.source ?? await inspectCandidateSource(target, run);
  const now = verificationWaitOptions.now ?? (() => Number(process.hrtime.bigint() / 1_000_000n));
  const deadline = continuation?.deadline ?? now() + 30 * 60_000;
  if (!Number.isFinite(deadline)) throw new TempoProblem("invalid_continuation", "The update continuation deadline is invalid.", { action: "Run: tempo start in a new shell without TEMPO_CLI_CONTINUATION." });
  const cancellation = new AbortController();
  const cancel = () => cancellation.abort();
  process.on("SIGINT", cancel);
  const checkUnchanged = () => assertInstallationUnchanged(installationState, stateDirectory, options.configPath);
  const checkSourceUnchanged = async () => {
    const current = await inspectCandidateSource(target, run);
    if (JSON.stringify(current) !== JSON.stringify(originalSource)) throw new TempoProblem("source_changed",
      "Local source changed while update verification was running. Local work is preserved; this start was cancelled.",
      { action: "Run: tempo start when you intend to update and the registered checkout is clean on main." });
  };
  const migrationPreflight = (currentRuntime, ownsLock = false) => {
    const maintenance = inspectMaintenance(stateDirectory);
    if (!ownsLock && ["active", "unknown"].includes(maintenance.status)) throw recoveryAssessment({ maintenance });
    const recovery = assessMigrationRecovery({ guard: readJson(join(stateDirectory, "migration-guard.json")),
      operation: readJson(join(stateDirectory, "operation.json")), target, configuration: currentRuntime.configuration,
      retry: options.flags.has("--retry") });
    if (recovery.status === "blocked") throw recoveryAssessment({ migrationRecovery: recovery });
  };
  try {
    for (;;) {
      checkUnchanged();
      migrationPreflight(runtime);
      let assessment, blockedProblem;
      try {
        assessment = await waitForVerification({ ...verificationWaitOptions, now, assess: signal => assessCandidate(target, run, undefined, { signal, expectedSource: originalSource }),
          checkUnchanged: async () => { checkUnchanged(); await checkSourceUnchanged(); }, signal: cancellation.signal, deadline, noWait: options.flags.has("--no-wait"), log });
        if (assessment.verification.status !== "verified") {
          blockedProblem = verificationProblem(assessment.verification);
          if (assessment.timedOut) {
            blockedProblem.message = "Release checks are still pending after the 30-minute wait. The update has not been applied.";
            blockedProblem.action = `Next: run tempo start later to wait again.${assessment.verification.run?.html_url ? ` Release checks: ${assessment.verification.run.html_url}` : ""}`;
          }
        }
      } catch (error) {
        if (["cancelled", "installation_changed", "source_changed"].includes(error.code)) throw error;
        blockedProblem = error;
      }
      if (cancellation.signal.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
      checkUnchanged();
      const release = acquireTargetLock(stateDirectory);
      try {
        checkUnchanged();
        await checkSourceUnchanged();
        await runtime.inspectTarget();
        migrationPreflight(runtime, true);
        let candidate;
        if (!blockedProblem) {
          try { candidate = await selectCandidate(target, run, undefined, { expectedRevision: assessment.revision, expectedSource: originalSource, signal: cancellation.signal }); }
          catch (error) {
            if (cancellation.signal.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
            if (error.code === "main_changed" || (error.code === "verification_pending" && !options.flags.has("--no-wait"))) {
              // Release the lock before any further wait and retain its deadline.
              continue;
            }
            await checkSourceUnchanged();
            if (error.code === "source_changed") throw error;
            blockedProblem = error;
          }
        }
        checkUnchanged();
        if (cancellation.signal.aborted) throw new TempoProblem("cancelled", "Waiting cancelled. No deployment was started.", { exitCode: 130 });
        if (candidate?.updated) {
          // Carry the original installation fence across the unlocked relaunch.
          // Only our verified fast-forward may advance the expected source.
          commandEnvironment.TEMPO_CLI_CONTINUATION = JSON.stringify({ installationState, deadline,
            source: { ...originalSource, head: candidate.revision } });
          release();
          const child = await execute(process.execPath, [join(target.root, "scripts/tempo-cli.mjs"), ...argumentsList], { allowFailure: true, echo: true });
          return child.code ?? 1;
        }
        if (blockedProblem) {
          if (!previous || options.command === "migrate") {
            throw new TempoProblem(blockedProblem.code ?? "update_blocked",
              !previous ? `No previous verified deployment is recorded, so the CLI has no verified fallback. This blocked attempt has not applied the update. ${blockedProblem.message}` : blockedProblem.message,
              { action: blockedProblem.action });
          }
          log(redact(blockedProblem.message, secrets));
          if (blockedProblem.verification?.message) log(redact(`Release evidence: ${blockedProblem.verification.message}`, secrets));
          if (blockedProblem.action) log(redact(blockedProblem.action, secrets));
          log(`Attempting previously verified revision ${previous.revision}; its images, schema and readiness must still pass. The update has not been applied.`);
        }
        const selectedRevision = candidate?.revision ?? previous.revision;
        runtime = createRuntime(target, { run, stateDirectory, previous, revision: selectedRevision,
          evidence: candidate?.evidence, fallback: !candidate, retry: options.flags.has("--retry"), log });
        await runtime.inspectTarget(); secrets.push(...runtime.secretValues);
        const recreate = options.command === "restart" || Boolean(candidate && previous?.revision !== selectedRevision);
        // SIGINT is scoped to waiting. Once maintenance starts, retain the CLI's
        // existing interruption/guard semantics rather than suppressing signals.
        process.off("SIGINT", cancel);
        await executeLifecycle({ recreate }, { ...runtime, ensureImages: async () => {
          const imagePreparation = await runtime.ensureImages();
          if (candidate && ((await run("git", ["rev-parse", "HEAD"])).stdout.trim() !== selectedRevision
            || (await run("git", ["status", "--porcelain"])).stdout.trim()))
            throw new Error("Checkout changed while images were prepared. Local work is preserved; no database maintenance or application shutdown was started.");
          return imagePreparation;
        } });
        log(`Tempo ready at ${target.webUrl} (revision ${selectedRevision.slice(0, 12)})${blockedProblem ? "; previous verified version ready, update deferred" : "; deployment verified"}.`);
        if (options.command !== "migrate" && !options.flags.has("--no-open") && process.platform === "darwin") await run("open", [target.webUrl]);
        return 0;
      } finally { release(); }
    }
  } finally { process.off("SIGINT", cancel); }

}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await main(); }
  catch (error) {
    const secrets = Object.entries(process.env).filter(([key]) => /TOKEN|PASSWORD|SECRET/i.test(key)).map(([, value]) => value);
    console.error(`Tempo: ${redact(error.message, secrets)}`);
    if (error.exitCode !== 130) console.error(redact(error.action ?? "Next: correct the named installation/credential conflict above before retrying; preserve study data and local work. See docs/POSTGRES-MAINTENANCE.md for storage recovery.", secrets));
    process.exitCode = error.exitCode ?? 1;
  }
}
