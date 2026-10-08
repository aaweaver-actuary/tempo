// Source, target identity, and durable deployment records for the local product.
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  realpathSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { TempoProblem, verificationProblem } from "./tempo-guidance.mjs";

export const productVolumes = Object.fromEntries(["tempo-postgres-data", "tempo-postgres-backups",
  "tempo-redis-data", "tempo-engine-operations"].map(name => [name, { name, external: true }]));

// Persistent data has exactly one owner and destination. Test aliases retain
// the same ownership contract on their independently scoped disposable volumes.
const persistentMounts = {
  postgres: { source: "tempo-postgres-data", disposableSource: "postgres-test-data", destination: "/var/lib/postgresql" },
  "postgres-backup": { source: "tempo-postgres-backups", disposableSource: "postgres-test-backups", destination: "/backups" },
  redis: { source: "tempo-redis-data", disposableSource: "redis-test-data", destination: "/data" },
  "defense-engine": { source: "tempo-engine-operations", disposableSource: "engine-test-operations", destination: "/state" },
};

export function schemaVersionFromSource(source) {
  const matches = [...source.matchAll(/^POSTGRES_SCHEMA_VERSION\s*=\s*(\d+)\s*$/gm)];
  if (matches.length !== 1 || Number(matches[0][1]) < 1) throw new Error("Cannot read the candidate PostgreSQL schema version.");
  return Number(matches[0][1]);
}

export function portsFromConfig(config) {
  return Object.entries(config.services ?? {}).flatMap(([service, definition]) =>
    (definition.ports ?? []).map(port => ({ service, host: port.host_ip ?? "0.0.0.0",
      port: String(port.published), target: Number(port.target) })));
}

function inside(path, directory) {
  const difference = relative(resolve(directory), resolve(path));
  return difference === "" || (!difference.startsWith("..") && !isAbsolute(difference));
}

function validateWorkerStorageContract(serviceName, service, secrets) {
  const environment = service?.environment ?? {};
  const expectedEnvironment = {
    TEMPO_DATABASE_WRITE_URL: "postgresql://tempo_writer@postgres:5432/tempo",
    TEMPO_DATABASE_READ_URL: "postgresql://tempo_writer@postgres:5432/tempo",
    PGPASSFILE: "/run/secrets/writer_pgpass",
    TEMPO_REDIS_URL: "redis://redis:6379/0",
  };
  for (const [name, expected] of Object.entries(expectedEnvironment)) {
    if (environment[name] !== expected)
      throw new Error(`Tempo ${serviceName} must set ${name} to ${expected} before maintenance.`);
  }
  // database.py supports this legacy SQLite override; reject its presence even
  // when empty so the worker contract cannot imply a compatibility fallback.
  if (Object.hasOwn(environment, "TEMPO_DB_PATH"))
    throw new Error(`Tempo ${serviceName} must not define the SQLite fallback TEMPO_DB_PATH.`);
  // libpq can use hostaddr instead of the explicit host, and a selected service
  // can supply a password or override PGPASSFILE. Reject competing inputs.
  for (const name of ["PGPASSWORD", "PGHOSTADDR", "PGSERVICE"]) {
    if (Object.hasOwn(environment, name))
      throw new Error(`Tempo ${serviceName} must not define ${name}; PostgreSQL worker connections must use the explicit DSNs and /run/secrets/writer_pgpass.`);
  }
  const passfileMounts = (service.secrets ?? []).filter(secret =>
    resolve("/run/secrets", secret.target ?? secret.source) === expectedEnvironment.PGPASSFILE);
  if (!secrets?.writer_pgpass || passfileMounts.length !== 1 || passfileMounts[0].source !== "writer_pgpass")
    throw new Error(`Tempo ${serviceName} must attach only writer_pgpass at /run/secrets/writer_pgpass.`);
  const mode = passfileMounts[0].mode;
  // Resolved Compose uses an octal string (or a numeric mode). Some versions
  // omit unsupported local modes; the global private-file check still applies.
  if (mode !== undefined && mode !== "0400" && mode !== 0o400)
    throw new Error(`Tempo ${serviceName} writer_pgpass must use private mode 0400.`);
}

export function validateTarget(config, target) {
  if (config.name !== target.project) throw new Error("Compose project differs from the registered Tempo target.");
  if (JSON.stringify(portsFromConfig(config)) !== JSON.stringify(target.ports))
    throw new Error(`Compose ports differ from the registered Tempo target: configured ${JSON.stringify(portsFromConfig(config))}; registered ${JSON.stringify(target.ports)}. Correct the registered port mapping before maintenance.`);
  for (const [key, expected] of Object.entries(target.volumes)) {
    const actual = config.volumes?.[key];
    if (!actual || actual.name !== expected.name || Boolean(actual.external) !== Boolean(expected.external))
      throw new Error(`Unexpected Tempo volume mapping: ${key}.`);
  }
  const mountedVolumes = Object.values(config.services).flatMap(service => service.volumes ?? [])
    .filter(volume => volume.type === "volume").map(volume => volume.source);
  if (mountedVolumes.some(key => !target.volumes[key])) throw new Error("Unregistered data volume in the Tempo target.");
  for (const [serviceName, service] of Object.entries(config.services)) {
    const expected = persistentMounts[serviceName];
    const source = target.disposable ? expected?.disposableSource : expected?.source;
    const mounts = (service.volumes ?? []).filter(mount => mount.type === "volume");
    if (mounts.some(mount => !expected || mount.source !== source || mount.target !== expected.destination)
      || (expected && mounts.length !== 1))
      throw new Error(`Persistent volume mount ownership differs for ${serviceName}. Restore its registered data mount before maintenance; preserve all volumes.`);
  }
  for (const [serviceName, expected] of Object.entries(persistentMounts)) {
    const source = target.disposable ? expected.disposableSource : expected.source;
    if (!config.services[serviceName] || !target.volumes[source])
      throw new Error(`Required persistent volume mount is missing for ${serviceName}.`);
  }
  const databaseMount = config.services.postgres?.volumes?.find(volume => volume.target === "/var/lib/postgresql");
  if (!databaseMount || databaseMount.type !== "volume" || databaseMount.source !== target.postgresVolumeKey
    || config.services.postgres.environment?.POSTGRES_DB !== "tempo")
    throw new Error("PostgreSQL data mount or database differs from the registered Tempo target.");
  const api = config.services.api?.environment ?? {};
  if (!/^postgresql:\/\/tempo_reader@postgres:5432\/tempo$/.test(api.TEMPO_DATABASE_READ_URL ?? "")
    || api.TEMPO_DATABASE_WRITE_URL || api.TEMPO_DB_PATH)
    throw new Error("Tempo API must use PostgreSQL reader credentials without a writer or SQLite fallback.");
  for (const serviceName of ["foreground-worker", "background-worker"])
    validateWorkerStorageContract(serviceName, config.services[serviceName], config.secrets);
  if (config.services["background-scheduler"]?.environment?.TEMPO_REDIS_URL !== "redis://redis:6379/0")
    throw new Error("Tempo background-scheduler must set TEMPO_REDIS_URL to redis://redis:6379/0 before maintenance.");
  if (target.disposable) {
    if (!target.project.startsWith("tempo-pg-regressions-") || api.TEMPO_TEST_INSTANCE !== "disposable"
      || Object.values(target.volumes).some(volume => !volume.name.startsWith(`${target.project}_`)))
      throw new Error("Invalid disposable target isolation.");
  } else if (api.TEMPO_TEST_INSTANCE) throw new Error("A study database cannot be marked disposable.");
  for (const [name, secret] of Object.entries(config.secrets ?? {})) {
    if (!secret.file || !isAbsolute(secret.file) || !existsSync(secret.file) || !statSync(secret.file).isFile())
      throw new Error(`Missing secret file for ${name}; check the registered environment file.`);
    if ((!target.disposable && inside(realpathSync(secret.file), realpathSync(target.root)))
      || (statSync(secret.file).mode & 0o077))
      throw new Error(`Secret file ${name} needs private permissions and a location outside the checkout.`);
  }
}

export function validateContainers(containers, target) {
  const volumes = new Set(Object.values(target.volumes).map(volume => volume.name));
  for (const container of containers) {
    const labels = container.Config?.Labels ?? {};
    const ownsVolume = (container.Mounts ?? []).some(mount => mount.Type === "volume" && volumes.has(mount.Name));
    const ownsPort = Object.values(container.NetworkSettings?.Ports ?? {}).flatMap(bindings => bindings ?? [])
      .some(binding => target.ports.some(port => String(binding.HostPort) === port.port));
    if ((ownsVolume || ownsPort) && labels["com.docker.compose.project"] !== target.project)
      throw new Error(`Another Docker container (${container.Name ?? container.Id}) uses Tempo's volume or port; its project is ${labels["com.docker.compose.project"] ?? "unregistered"}.`);
    if (labels["com.docker.compose.project"] === target.project) {
      const workingDirectory = labels["com.docker.compose.project.working_dir"];
      if (workingDirectory && resolve(workingDirectory) !== resolve(target.root))
        throw new Error("Registered Compose project belongs to another checkout.");
      for (const mount of container.Mounts ?? []) {
        // Older backup containers inherit PostgreSQL's unused anonymous image
        // volume. It contains no cluster; new configurations use tmpfs here.
        const unusedBackupScratch = labels["com.docker.compose.service"] === "postgres-backup"
          && mount.Destination === "/var/lib/postgresql" && /^[a-f0-9]{64}$/.test(mount.Name ?? "");
        if (mount.Type === "volume" && !volumes.has(mount.Name) && !unusedBackupScratch)
          throw new Error("A Tempo container has an unregistered volume; stop and inspect the target.");
      }
    }
  }
}

export function deploymentCanStart(record, schema, availableImages) {
  return Boolean(record && record.schema === schema && record.revision
    && Object.keys(record.images ?? {}).length && Object.values(record.images).every(image => availableImages.includes(image)));
}

export function atomicJson(path, value) {
  const parentDirectory = dirname(path);
  mkdirSync(parentDirectory, { recursive: true, mode: 0o700 });
  const temporary = `${path}.${randomUUID()}.partial`;
  let temporaryCreated = false;
  try {
    const descriptor = openSync(temporary, "wx", 0o600);
    temporaryCreated = true;
    try { writeFileSync(descriptor, `${JSON.stringify(value, null, 2)}\n`); fsyncSync(descriptor); }
    finally { closeSync(descriptor); }
    renameSync(temporary, path);
    temporaryCreated = false;
    // The renamed entry must be durable before a migration can commit. Node's
    // read-only directory descriptor works on macOS and Linux; fail closed on
    // all errors, including filesystems that do not support directory fsync.
    try {
      const directoryDescriptor = openSync(parentDirectory, "r");
      try { fsyncSync(directoryDescriptor); }
      finally { closeSync(directoryDescriptor); }
    } catch (error) {
      throw new Error(`Deployment state durability failed for ${path}: could not sync its containing directory. ${error.message}`, { cause: error });
    }
  } catch (error) {
    if (temporaryCreated) {
      try { unlinkSync(temporary); }
      catch (cleanupError) { throw new AggregateError([error, cleanupError], "Deployment state write failed and temporary-file cleanup also failed."); }
    }
    throw error;
  }
}

export function acquireTargetLock(directory) {
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const path = resolve(directory, "maintenance.lock");
  const token = randomUUID();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const descriptor = openSync(path, "wx", 0o600);
      try { writeFileSync(descriptor, JSON.stringify({ pid: process.pid, token })); fsyncSync(descriptor); }
      finally { closeSync(descriptor); }
      return () => { if (existsSync(path) && JSON.parse(readFileSync(path, "utf8")).token === token) unlinkSync(path); };
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      let owner;
      try { owner = JSON.parse(readFileSync(path, "utf8")); }
      catch { throw new TempoProblem("maintenance_unknown", "Tempo maintenance lock is incomplete.", { action: `Next: preserve ${path} and identify its owning process before retrying; do not remove an active lock.` }); }
      try { process.kill(owner.pid, 0); }
      catch (ownerError) {
        if (ownerError.code === "ESRCH" && JSON.parse(readFileSync(path, "utf8")).token === owner.token) {
          unlinkSync(path); continue;
        }
      }
      throw new TempoProblem("maintenance_active", "Tempo maintenance is already in progress for this target.", { action: "Next: let that command finish. Use tempo status to check progress." });
    }
  }
  throw new Error("Could not acquire Tempo maintenance lock.");
}

export function targetKey(target) {
  return createHash("sha256").update(JSON.stringify([target.daemonId ?? target.context,
    Object.values(target.volumes).map(volume => volume.name).sort()])).digest("hex").slice(0, 24);
}

const requiredVerificationJobs = ["plan", "frontend / verify", "backend / verify", "build / verify",
  "postgres / verify", "lifecycle / verify", "browser / verify", "visual / verify", "quality"];

export function qualityEvidence(revision, run, jobs) {
  if (run.head_sha !== revision || run.head_branch !== "main" || !["push", "workflow_dispatch", "schedule"].includes(run.event))
    throw new Error("CI evidence is for a different revision or branch.");
  if (run.status !== "completed") throw new Error("The newest main revision is still being verified.");
  for (const name of requiredVerificationJobs) {
    const job = jobs.find(candidate => candidate.name === name);
    if (!job || job.status !== "completed" || job.conclusion !== "success")
      throw new Error(`Required CI job ${name} has not passed. ${run.html_url}`);
  }
  return { commit: revision, run_id: run.id, url: run.html_url };
}

export function redact(text, secretValues = []) {
  let safe = String(text);
  for (const value of secretValues.filter(value => value && value.length >= 4).sort((a, b) => b.length - a.length))
    safe = safe.replaceAll(value, "[redacted]");
  return safe.replace(/(Bearer\s+)[^\s"']+/gi, "$1[redacted]")
    .replace(/(https?:\/\/)[^/\s@]+@/gi, "$1[redacted]@")
    .replace(/(postgres(?:ql)?:\/\/[^:\s/]+:)[^@\s]+@/gi, "$1[redacted]@");
}

// Provenance distinguishes process failures from exceptions in our own code.
export class CommandExecutionError extends Error {
  constructor(command, message, { cause, status } = {}) {
    super(message, { cause });
    this.command = command;
    this.status = status;
  }
}

export function isOperationalGitFailure(error, signal) {
  return error instanceof CommandExecutionError && error.command === "git"
    && (error.status !== undefined || ["ENOENT", "ENOTDIR", "EACCES", "EPERM", "EIO", "ENOEXEC", "EMFILE", "ENFILE", "EAGAIN", "ETXTBSY"].includes(error.cause?.code)
      || (signal?.aborted && error.cause?.code === "ABORT_ERR"));
}

export function isCandidateBlocker(error) {
  return (error instanceof TempoProblem && ["source_unavailable", "source_branch", "source_changes", "source_remote", "source_diverged",
    "verification_pending", "verification_failed", "verification_missing", "verification_unavailable"].includes(error.code))
    || isOperationalGitFailure(error);
}

// Discrete arguments keep paths, passwords, and shell punctuation out of shell evaluation.
export function commandExecutor({ root, environment = process.env, output = console.log, secretValues = [] }) {
  return (command, argumentsList, { allowFailure = false, echo = false, timeout = 0, signal } = {}) => new Promise((resolveResult, reject) => {
    const child = spawn(command, argumentsList, { cwd: root, env: environment, stdio: ["ignore", "pipe", "pipe"], timeout, signal });
    let stdout = "", stderr = "", pendingOutput = "";
    const consume = (chunk, isError) => {
      const text = chunk.toString();
      if (isError) stderr = (stderr + text).slice(-2_000_000);
      else stdout = (stdout + text).slice(-20_000_000);
      if (echo) {
        pendingOutput += text;
        const lines = pendingOutput.split("\n"); pendingOutput = lines.pop();
        for (const line of lines) output(redact(line, secretValues));
      }
    };
    child.stdout.on("data", chunk => consume(chunk, false)); child.stderr.on("data", chunk => consume(chunk, true));
    child.once("error", error => reject(new CommandExecutionError(command, `${command} is unavailable: ${redact(error.message, secretValues)}`, { cause: error })));
    child.once("close", code => {
      if (echo && pendingOutput) output(redact(pendingOutput, secretValues));
      if (code !== 0 && !allowFailure) reject(new CommandExecutionError(command, `${command} failed (${code ?? "interrupted"}): ${redact(stderr || stdout, secretValues).slice(-4000)}`, { status: code }));
      else resolveResult({ code, stdout, stderr });
    });
  });
}

async function githubJson(path, { signal } = {}) {
  const token = process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN;
  const response = await fetch(`https://api.github.com/repos/aaweaver-actuary/tempo/${path}`, {
    headers: { Accept: "application/vnd.github+json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15_000)]) : AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const rateLimited = response.status === 429 || (response.status === 403
      && (response.headers.get("x-ratelimit-remaining") === "0" || response.headers.has("retry-after")));
    const cause = rateLimited ? "rate limit" : [401, 403].includes(response.status)
      ? "authentication or access denied" : response.status >= 500 ? "server unavailable" : "request rejected";
    throw new Error(`GitHub verification unavailable: ${cause} (HTTP ${response.status}).`);
  }
  return response.json();
}

export async function assessMainVerification(revision, fetchJson = githubJson, { signal } = {}) {
  let firstAssessment, unavailableAssessment;
  const unavailable = error => ({ status: "unavailable", revision,
    message: error.message.startsWith("GitHub verification unavailable") ? error.message : `GitHub verification unavailable: ${error.message}` });
  // Racing the signal also bounds injected clients which ignore cancellation.
  const requestJson = async path => {
    signal?.throwIfAborted();
    if (!signal) return fetchJson(path);
    let abortRequest;
    const aborted = new Promise((_, reject) => {
      abortRequest = () => reject(signal.reason);
      signal.addEventListener("abort", abortRequest, { once: true });
    });
    try { return await Promise.race([fetchJson(path, { signal }), aborted]); }
    finally { signal.removeEventListener("abort", abortRequest); }
  };
  // Any complete successful allowed run for this exact main revision is proof.
  // A later pending/failed rerun does not invalidate immutable successful proof.
  for (let runPage = 1; ; runPage += 1) {
    let result;
    try { result = await requestJson(`actions/workflows/pages.yml/runs?head_sha=${revision}&branch=main&per_page=100&page=${runPage}`); }
    catch (error) { return unavailable(error); }
    if (!Array.isArray(result?.workflow_runs)) return unavailable(new Error("GitHub returned an invalid workflow-run response."));
    for (const workflowRun of result.workflow_runs) {
      if (workflowRun.head_sha !== revision || workflowRun.head_branch !== "main"
        || !["push", "workflow_dispatch", "schedule"].includes(workflowRun.event)) continue;
      if (workflowRun.status !== "completed") {
        firstAssessment ??= { status: "pending", revision, run: workflowRun,
          message: `Main revision verification is still pending. ${workflowRun.html_url}` };
        continue;
      }
      try {
        const jobs = [];
        for (let jobPage = 1; ; jobPage += 1) {
          const page = await requestJson(`actions/runs/${workflowRun.id}/jobs?filter=latest&per_page=100&page=${jobPage}`);
          if (!Array.isArray(page?.jobs)) throw new Error("GitHub returned an invalid jobs response.");
          jobs.push(...page.jobs);
          if (page.jobs.length < 100) break;
        }
        try {
          const evidence = qualityEvidence(revision, workflowRun, jobs);
          return { status: "verified", revision, evidence, run: workflowRun, message: `Exact-main verification passed. ${evidence.url}` };
        } catch (error) {
          const name = requiredVerificationJobs.find(requiredName => {
            const job = jobs.find(candidate => candidate.name === requiredName);
            return !job || job.status !== "completed" || job.conclusion !== "success";
          });
          const job = jobs.find(candidate => candidate.name === name);
          firstAssessment ??= { status: !job ? "missing" : job.status !== "completed" ? "pending" : "failed",
            revision, run: workflowRun, job: name, conclusion: job?.conclusion ?? "missing",
            message: `${error.message}${job ? ` (${job.conclusion ?? job.status})` : " (job missing)"}` };
        }
      } catch (error) { unavailableAssessment ??= unavailable(error); }
    }
    if (result.workflow_runs.length < 100) break;
  }
  return unavailableAssessment ?? firstAssessment ?? { status: "missing", revision,
    message: "No complete main CI verification exists for the candidate revision." };
}

async function verifiedMainEvidence(revision, fetchJson, signal) {
  const assessment = await assessMainVerification(revision, fetchJson, { signal: signal
    ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000) });
  if (assessment.status !== "verified") throw verificationProblem(assessment);
  return assessment.evidence;
}

export function sourceFingerprint(source) {
  const { branch, head, changes, origin } = source;
  return [branch, head, changes, origin].every(value => typeof value === "string")
    ? { branch, head, changes, origin } : null;
}

export function sourceMatchesFingerprint(source, expected) {
  const observed = sourceFingerprint(source), fence = sourceFingerprint(expected);
  return Boolean(observed && fence && observed.branch === fence.branch && observed.head === fence.head
    && observed.changes === fence.changes && observed.origin === fence.origin);
}

export async function inspectCandidateSource(target, run, { signal } = {}) {
  const failures = [];
  const probe = async argumentsList => {
    signal?.throwIfAborted();
    let result;
    try { result = await run("git", argumentsList, { allowFailure: true, timeout: 5000, signal }); }
    catch (error) {
      signal?.throwIfAborted();
      if (!isOperationalGitFailure(error)) throw error;
      failures.push({ probe: argumentsList.join(" "), message: redact(error.message).slice(-4000), cause: error });
      return null;
    }
    signal?.throwIfAborted();
    if (result.code !== 0) {
      const cause = new CommandExecutionError("git", `git failed (${result.code ?? "interrupted"}): ${redact(result.stderr || result.stdout).slice(-4000)}`, { status: result.code });
      failures.push({ probe: argumentsList.join(" "), message: cause.message, cause });
      return null;
    }
    return result.stdout.trim();
  };
  const branch = await probe(["branch", "--show-current"]);
  let head = await probe(["rev-parse", "HEAD"]);
  if (head !== null && !/^[a-f0-9]{40}$/.test(head)) {
    failures.push({ probe: "rev-parse HEAD", message: "Git did not report a valid HEAD revision." });
    head = null;
  }
  const changes = await probe(["--no-optional-locks", "status", "--porcelain"]);
  const sourceChangeEntries = changes?.split("\n") ?? [];
  const origin = await probe(["remote", "get-url", "origin"]);
  const action = `Next: preserve your work in ${target.root}; move development work to an isolated checkout, then leave this registered checkout clean on main.`;
  let problem;
  if (failures.length) problem = new TempoProblem("source_unavailable", "Local source state could not be inspected safely. The update is blocked; the checkout is preserved.",
    { action: `Next: preserve the checkout at ${target.root}; repair Git/repository access before attempting an update.`, cause: failures[0].cause });
  else if (branch !== "main") problem = new TempoProblem("source_branch", `Automatic updates require the registered checkout to be on main. Current branch: ${branch || "detached HEAD"}.`, { action });
  else if (changes) problem = new TempoProblem("source_changes", `Local changes are preserved: ${sourceChangeEntries.slice(0, 3).join("; ")}${sourceChangeEntries.length > 3 ? "; more files listed in tempo doctor --verbose" : ""}. Save them on a separate branch before updating Tempo.`, { action });
  else if (!/^(?:https?:\/\/github\.com\/|git@github\.com:)aaweaver-actuary\/tempo(?:\.git)?$/.test(origin))
    problem = new TempoProblem("source_remote", "Registered checkout has an unexpected GitHub remote.",
      { action: `Next: correct the origin for ${target.root} to the verified Tempo repository before updating.` });
  return { branch, head, changes, origin, problem, failures };
}

export async function assessCandidate(target, run, fetchJson, { signal, expectedSource } = {}) {
  const source = await inspectCandidateSource(target, run, { signal });
  if (expectedSource && !sourceMatchesFingerprint(source, expectedSource))
    throw new TempoProblem("source_changed", "Local source changed while update verification was running. Local work is preserved; main was not fast-forwarded.",
      { action: "Next: preserve the source changes, then run tempo start when the registered checkout is clean on main." });
  if (source.problem) throw source.problem;
  const requestSignal = signal ? AbortSignal.any([signal, AbortSignal.timeout(30_000)]) : AbortSignal.timeout(30_000);
  let revision;
  try {
    const remote = await run("git", ["ls-remote", "https://github.com/aaweaver-actuary/tempo", "refs/heads/main"],
      { allowFailure: true, timeout: 15_000, signal: requestSignal });
    revision = remote.stdout.trim().split(/\s+/)[0];
    if (remote.code !== 0 || !/^[a-f0-9]{40}$/.test(revision ?? ""))
      return { source, revision: source.head, verification: { status: "unavailable", message: `Remote main could not be read${remote.stderr?.trim() ? `: ${remote.stderr.trim().slice(-400)}` : "."}` } };
  } catch (error) {
    signal?.throwIfAborted();
    if (!isOperationalGitFailure(error, requestSignal)) throw error;
    return { source, revision: source.head, verification: { status: "unavailable", message: error.message } };
  }
  // Do not fetch while waiting. Unknown ancestry is checked by the locked selector.
  if (source.head !== revision && (await run("git", ["cat-file", "-e", `${revision}^{commit}`], { allowFailure: true, timeout: 5000 })).code === 0
    && (await run("git", ["merge-base", "--is-ancestor", source.head, revision], { allowFailure: true, timeout: 5000 })).code === 1)
    throw new TempoProblem("source_diverged", "Local main has diverged; Tempo will not reset or merge your work.",
      { action: `Next: preserve and reconcile ${target.root} with remote main without discarding local commits.` });
  return { source, revision, verification: await assessMainVerification(revision, fetchJson, { signal: requestSignal }) };
}

export async function selectCandidate(target, run, fetchJson = githubJson, { expectedRevision, expectedSource, signal } = {}) {
  signal?.throwIfAborted();
  if (!target.root) throw new Error("Tempo checkout is not registered.");
  const source = await inspectCandidateSource(target, run, { signal });
  if (source.problem) throw source.problem;
  if (expectedSource && !sourceMatchesFingerprint(source, expectedSource))
    throw new TempoProblem("source_changed", "Local source changed while update verification was running. Local work is preserved; main was not fast-forwarded.");
  await run("git", ["fetch", "origin", "main"], { timeout: 60_000, signal });
  const revision = (await run("git", ["rev-parse", "FETCH_HEAD"])).stdout.trim();
  const current = (await run("git", ["rev-parse", "HEAD"])).stdout.trim();
  if (expectedRevision && revision !== expectedRevision)
    throw new TempoProblem("main_changed", "Main advanced while verification was being checked.");
  if (current !== source.head)
    throw new TempoProblem("source_changed", "Local source changed while update verification was running. Local work is preserved; main was not fast-forwarded.");
  if ((await run("git", ["merge-base", "--is-ancestor", current, revision], { allowFailure: true })).code !== 0)
    throw new TempoProblem("source_diverged", "Local main has diverged; Tempo will not reset or merge your work.");
  const evidence = await verifiedMainEvidence(revision, fetchJson, signal);
  signal?.throwIfAborted();
  if (expectedRevision) {
    const latest = (await run("git", ["ls-remote", "https://github.com/aaweaver-actuary/tempo", "refs/heads/main"], { timeout: 15_000, signal })).stdout.trim().split(/\s+/)[0];
    if (latest !== revision) throw new TempoProblem("main_changed", "Main advanced while verification was being checked.");
  }
  if (revision !== current) {
    const checkedSource = await inspectCandidateSource(target, run, { signal });
    if (!sourceMatchesFingerprint(checkedSource, source))
      throw new TempoProblem("source_changed", "Local source changed while update verification was running. Local work is preserved; main was not fast-forwarded.");
    signal?.throwIfAborted();
    await run("git", ["merge", "--ff-only", revision]);
    if ((await run("git", ["rev-parse", "HEAD"])).stdout.trim() !== revision)
      throw new Error("Checkout revision changed during update; no deployment was started.");
  }
  return { revision, evidence, updated: revision !== current };
}
