// Source, target identity, and durable deployment records for the local product.
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  realpathSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";

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

export function validateTarget(config, target) {
  if (config.name !== target.project) throw new Error("Compose project differs from the registered Tempo target.");
  if (JSON.stringify(portsFromConfig(config)) !== JSON.stringify(target.ports))
    throw new Error("Compose ports differ from the registered Tempo target. Run tempo doctor before changing the installation.");
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
      throw new Error(`Persistent volume mount ownership differs for ${serviceName}. Inspect tempo doctor before maintenance.`);
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
      catch { throw new Error("Tempo maintenance lock is incomplete; inspect it with tempo doctor before retrying."); }
      try { process.kill(owner.pid, 0); }
      catch (ownerError) {
        if (ownerError.code === "ESRCH" && JSON.parse(readFileSync(path, "utf8")).token === owner.token) {
          unlinkSync(path); continue;
        }
      }
      throw new Error("Tempo maintenance is already in progress for this target.");
    }
  }
  throw new Error("Could not acquire Tempo maintenance lock.");
}

export function targetKey(target) {
  return createHash("sha256").update(JSON.stringify([target.daemonId ?? target.context,
    Object.values(target.volumes).map(volume => volume.name).sort()])).digest("hex").slice(0, 24);
}

export function qualityEvidence(revision, run, jobs) {
  if (run.head_sha !== revision || run.head_branch !== "main" || !["push", "workflow_dispatch", "schedule"].includes(run.event))
    throw new Error("CI evidence is for a different revision or branch.");
  if (run.status !== "completed") throw new Error("The newest main revision is still being verified.");
  for (const name of ["plan", "frontend / verify", "backend / verify", "build / verify",
    "postgres / verify", "browser / verify", "visual / verify", "quality"]) {
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
    .replace(/(postgres(?:ql)?:\/\/[^:\s/]+:)[^@\s]+@/gi, "$1[redacted]@");
}

// Discrete arguments keep paths, passwords, and shell punctuation out of shell evaluation.
export function commandExecutor({ root, environment = process.env, output = console.log, secretValues = [] }) {
  return (command, argumentsList, { allowFailure = false, echo = false, timeout = 0 } = {}) => new Promise((resolveResult, reject) => {
    const child = spawn(command, argumentsList, { cwd: root, env: environment, stdio: ["ignore", "pipe", "pipe"], timeout });
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
    child.once("error", error => reject(new Error(`${command} is unavailable: ${redact(error.message, secretValues)}`)));
    child.once("close", code => {
      if (echo && pendingOutput) output(redact(pendingOutput, secretValues));
      if (code !== 0 && !allowFailure) reject(new Error(`${command} failed (${code ?? "interrupted"}): ${redact(stderr || stdout, secretValues).slice(-4000)}`));
      else resolveResult({ code, stdout, stderr });
    });
  });
}

async function githubJson(path) {
  const token = process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN;
  const response = await fetch(`https://api.github.com/repos/aaweaver-actuary/tempo/${path}`, {
    headers: { Accept: "application/vnd.github+json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`GitHub verification unavailable (HTTP ${response.status}).`);
  return response.json();
}

async function verifiedMainEvidence(revision, fetchJson) {
  let verificationFailure;
  // Any complete successful allowed run for this exact main revision is proof.
  // A later pending/failed rerun does not invalidate immutable successful proof.
  for (let runPage = 1; ; runPage += 1) {
    const result = await fetchJson(`actions/workflows/pages.yml/runs?head_sha=${revision}&branch=main&per_page=100&page=${runPage}`);
    for (const workflowRun of result.workflow_runs) {
      if (workflowRun.head_sha !== revision || workflowRun.head_branch !== "main"
        || !["push", "workflow_dispatch", "schedule"].includes(workflowRun.event)) continue;
      try {
        if (workflowRun.status !== "completed") throw new Error(`Main revision verification is still pending. ${workflowRun.html_url}`);
        const jobs = [];
        for (let jobPage = 1; ; jobPage += 1) {
          const page = await fetchJson(`actions/runs/${workflowRun.id}/jobs?filter=latest&per_page=100&page=${jobPage}`);
          jobs.push(...page.jobs);
          if (page.jobs.length < 100) break;
        }
        return qualityEvidence(revision, workflowRun, jobs);
      } catch (error) { verificationFailure ??= error; }
    }
    if (result.workflow_runs.length < 100) break;
  }
  throw verificationFailure ?? new Error("No complete main CI verification exists for the candidate revision.");
}

export async function selectCandidate(target, run, fetchJson = githubJson) {
  if (!target.root) throw new Error("Tempo checkout is not registered.");
  const branch = (await run("git", ["branch", "--show-current"])).stdout.trim();
  if (branch !== "main") throw new Error("Automatic updates require the registered checkout to be on main.");
  if ((await run("git", ["status", "--porcelain"])).stdout.trim())
    throw new Error("Local changes are preserved. Save them on a separate branch before updating Tempo.");
  const remote = (await run("git", ["remote", "get-url", "origin"])).stdout.trim();
  if (!/^(?:https?:\/\/github\.com\/|git@github\.com:)aaweaver-actuary\/tempo(?:\.git)?$/.test(remote))
    throw new Error("Registered checkout has an unexpected GitHub remote.");
  await run("git", ["fetch", "origin", "main"], { timeout: 60_000 });
  const revision = (await run("git", ["rev-parse", "FETCH_HEAD"])).stdout.trim();
  const current = (await run("git", ["rev-parse", "HEAD"])).stdout.trim();
  if ((await run("git", ["merge-base", "--is-ancestor", current, revision], { allowFailure: true })).code !== 0)
    throw new Error("Local main has diverged; Tempo will not reset or merge your work.");
  const evidence = await verifiedMainEvidence(revision, fetchJson);
  if (revision !== current) {
    const checkedHead = (await run("git", ["rev-parse", "HEAD"])).stdout.trim();
    const checkedBranch = (await run("git", ["branch", "--show-current"])).stdout.trim();
    const checkedStatus = (await run("git", ["status", "--porcelain"])).stdout.trim();
    if (checkedHead !== current || checkedBranch !== "main" || checkedStatus)
      throw new Error("Local source changed while update verification was running. Local work is preserved; main was not fast-forwarded.");
    await run("git", ["merge", "--ff-only", revision]);
    if ((await run("git", ["rev-parse", "HEAD"])).stdout.trim() !== revision)
      throw new Error("Checkout revision changed during update; no deployment was started.");
  }
  return { revision, evidence, updated: revision !== current };
}
