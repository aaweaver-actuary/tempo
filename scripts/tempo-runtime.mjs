// Bounded deployment stages. No work is scheduled from application startup.
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { basename, join } from "node:path";
import { atomicJson, deploymentCanStart, redact, validateContainers, validateTarget } from "./tempo-deployment.mjs";

export const applicationServices = ["web", "defense-engine", "maia-worker", "api",
  "foreground-worker", "background-worker", "background-scheduler"];

export function configurationFingerprint(configured) {
  const stable = structuredClone(configured);
  for (const service of Object.values(stable.services)) {
    delete service.image;
    if (service.build) delete service.build.labels;
    if (service.labels) delete service.labels["org.opencontainers.image.revision"];
  }
  return createHash("sha256").update(JSON.stringify(stable)).digest("hex");
}

export async function executeLifecycle(plan, actions) {
  let interruptedApplications = false;
  try {
    await actions.ensureImages();
    await actions.ensureDatabase();
    const status = await actions.checkSchema();
    if (status.pending_versions.length || plan.recreate) {
      interruptedApplications = true;
      await actions.stopApplications();
      if (status.pending_versions.length) {
        await actions.backup();
        await actions.migrate();
        const updated = await actions.checkSchema();
        if (updated.pending_versions.length) throw new Error("Migrations did not reach the required schema version.");
      }
    }
    interruptedApplications = true;
    await actions.startServices({ recreate: Boolean(plan.recreate) });
    await actions.verifyReady();
    await actions.commitDeployment();
  } catch (error) {
    if (interruptedApplications) {
      try { await actions.stopApplications({ preservePhase: true }); }
      catch (stopError) { error = new AggregateError([error, stopError], "Deployment failed and application shutdown also failed."); }
    }
    await actions.recordFailure(error);
    throw error;
  }
}

export function createRuntime(target, { run, stateDirectory, revision, evidence, previous = null,
  fallback = false, preparedImages = null, log = console.log, fetcher = fetch, readinessMilliseconds = 180_000 }) {
  let composeFiles = fallback ? previous.composeFiles : target.composeFiles;
  let imageOverride = fallback ? previous.imageOverride : null;
  let configuration;
  let containers = [];
  let images = {};
  let status;
  let backupRecord;
  let stoppedServices = [];
  const secretValues = [];
  const journalPath = join(stateDirectory, "operation.json");
  const operation = { id: randomUUID(), revision, started_at: new Date().toISOString(), phase: "checking", target: target.project };
  const docker = (args, options) => run("docker", ["--context", target.context, ...args], options);
  const compose = (args, options) => docker(["compose", "--project-directory", target.root,
    ...(target.envFile ? ["--env-file", target.envFile] : []), "-p", target.project,
    ...composeFiles.flatMap(path => ["-f", path]), ...(imageOverride ? ["-f", imageOverride] : []), ...args], options);
  const stage = name => { operation.phase = name; atomicJson(journalPath, operation); log(`Tempo: ${name.replaceAll("_", " ")}`); };

  async function config() {
    configuration = JSON.parse((await compose(["--profile", "maintenance", "config", "--format", "json"])).stdout);
    validateTarget(configuration, target);
    if (configuration.services.postgres.image?.startsWith("postgres:")
      && !new RegExp(`^postgres:${target.postgresMajor}(?:\\.|-)`).test(configuration.services.postgres.image))
      throw new Error("Candidate PostgreSQL major upgrade is unsupported; use the separate major-upgrade procedure.");
    for (const secret of Object.values(configuration.secrets ?? {})) {
      const text = readFileSync(secret.file, "utf8").trim();
      secretValues.push(text);
      for (const line of text.split("\n")) if (line.split(":").length >= 5) secretValues.push(line.split(":").slice(4).join(":"));
    }
    for (const service of Object.values(configuration.services)) for (const [key, value] of Object.entries(service.environment ?? {}))
      if (/TOKEN|PASSWORD|SECRET|AUTHORIZATION/i.test(key)) secretValues.push(String(value));
    return configuration;
  }

  async function inspectTarget() {
    const daemonId = (await docker(["info", "--format", "{{.ID}}"])).stdout.trim();
    if (!target.daemonId || daemonId !== target.daemonId) throw new Error("Docker daemon differs from the registered Tempo target.");
    await config();
    const ids = (await docker(["ps", "-aq"])).stdout.trim().split(/\s+/).filter(Boolean);
    containers = ids.length ? JSON.parse((await docker(["inspect", ...ids])).stdout) : [];
    validateContainers(containers, target);
    for (const expected of Object.values(target.volumes)) {
      const result = await docker(["volume", "inspect", expected.name], { allowFailure: true });
      if (result.code !== 0) throw new Error(`Existing Tempo volume ${expected.name} is missing. It will not be replaced with empty data.`);
    }
    for (const port of target.ports) {
      const boundByTarget = containers.some(container => container.State?.Running
        && container.Config?.Labels?.["com.docker.compose.project"] === target.project
        && Object.values(container.NetworkSettings?.Ports ?? {}).flatMap(bindings => bindings ?? [])
          .some(binding => String(binding.HostPort) === port.port && (binding.HostIp === port.host || binding.HostIp === "0.0.0.0")));
      if (!boundByTarget) await new Promise((resolvePort, reject) => {
        const server = createServer();
        server.once("error", () => reject(new Error(`Tempo port ${port.port} is already in use or unavailable.`)));
        server.listen(Number(port.port), port.host, () => server.close(resolvePort));
      });
    }
    return { configuration, containers };
  }

  async function ensureImages() {
    await config();
    const desiredFingerprint = configurationFingerprint(configuration);
    const reusable = previous && previous.revision === revision && previous.configFingerprint === desiredFingerprint;
    if (fallback || reusable) {
      for (const image of Object.values(previous.images)) await docker(["image", "inspect", image]);
      images = previous.images;
      composeFiles = previous.composeFiles; imageOverride = previous.imageOverride;
      await config();
      return;
    }
    stage("preparing_images");
    const releaseDirectory = join(stateDirectory, "releases", `${revision}-${operation.id}`);
    mkdirSync(releaseDirectory, { recursive: true, mode: 0o700 });
    composeFiles = target.composeFiles.map((source, index) => {
      const destination = join(releaseDirectory, `${index}-${basename(source)}`);
      writeFileSync(destination, readFileSync(source), { mode: 0o600 }); return destination;
    });
    // The disposable runner builds once for its whole invocation. Accept its
    // candidate image receipt only after verifying every built image's revision.
    if (preparedImages) {
      if (preparedImages.revision !== revision || preparedImages.configFingerprint !== desiredFingerprint)
        throw new Error("Prepared image receipt differs from the candidate source or configuration.");
      for (const [name, service] of Object.entries(configuration.services)) {
        const image = preparedImages.images[name];
        if (!image) throw new Error(`Prepared image receipt lacks ${name}.`);
        const inspected = JSON.parse((await docker(["image", "inspect", image])).stdout)[0];
        if (service.build && inspected.Config?.Labels?.["org.opencontainers.image.revision"] !== revision)
          throw new Error(`Prepared image ${name} has the wrong revision.`);
        images[name] = inspected.Id;
      }
      imageOverride = join(releaseDirectory, "images.json");
      atomicJson(imageOverride, { services: Object.fromEntries(Object.entries(images).map(([name, image]) => [name, { image }])) });
      await config(); return;
    }
    const labelsFile = join(releaseDirectory, "build-labels.json");
    const buildServices = Object.entries(configuration.services).filter(([, service]) => service.build).map(([name]) => name);
    atomicJson(labelsFile, { services: Object.fromEntries(buildServices.map(name => [name,
      { image: `${target.project}-${name}:tempo-${revision}`, build: { labels: { "org.opencontainers.image.revision": revision } } }])) });
    composeFiles.push(labelsFile);
    await compose(["--profile", "maintenance", "build", ...buildServices], { echo: true });
    const pulledServices = Object.entries(configuration.services).filter(([, service]) => !service.build && service.image).map(([name]) => name);
    if (pulledServices.length) await compose(["--profile", "maintenance", "pull", ...pulledServices], { echo: true });
    await config();
    for (const [name, service] of Object.entries(configuration.services)) {
      if (!service.image) throw new Error(`Compose did not resolve an image for ${name}.`);
      const inspected = JSON.parse((await docker(["image", "inspect", service.image])).stdout)[0];
      if (service.build && inspected.Config?.Labels?.["org.opencontainers.image.revision"] !== revision)
        throw new Error(`Image ${name} was not built from the selected revision.`);
      images[name] = inspected.Id;
    }
    imageOverride = join(releaseDirectory, "images.json");
    atomicJson(imageOverride, { services: Object.fromEntries(Object.entries(images).map(([name, image]) => [name, { image }])) });
    await config();
  }

  async function ensureDatabase() {
    stage("checking_database");
    const volume = target.volumes[target.postgresVolumeKey];
    const image = configuration.services.postgres.image;
    const expectedMajor = target.postgresMajor;
    // An existing named volume is not proof of an initialized cluster. Mount it
    // read-only before PostgreSQL is allowed to initialize anything.
    const cluster = await docker(["run", "--rm", "--read-only", "--network", "none",
      "--mount", `type=volume,src=${volume.name},dst=/data,readonly`, "--entrypoint", "sh", image,
      "-ec", 'files=$(find /data -maxdepth 4 -type f -name PG_VERSION); test "$(printf "%s\\n" "$files" | grep -c .)" = 1; cat "$files"']);
    if (Number(cluster.stdout.trim()) !== expectedMajor)
      throw new Error("Existing PostgreSQL cluster does not match the registered major version; a separate major-upgrade procedure is required.");
    if (!fallback && !new RegExp(`^postgres:${expectedMajor}(?:\\.|-)`).test(target.postgresImage))
      throw new Error("Candidate PostgreSQL major upgrade is unsupported.");
    await compose(["up", "-d", "--no-build", "--no-deps", "--wait", "--wait-timeout", "180", "postgres", "redis"], { echo: true });
    const pong = (await compose(["exec", "-T", "redis", "redis-cli", "ping"])).stdout.trim();
    if (pong !== "PONG") throw new Error("Redis is not ready: expected PONG.");
  }

  async function maintenance(script, args = [], options = {}) {
    return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T", "migration", script, ...args], options);
  }

  async function checkSchema() {
    status = JSON.parse((await maintenance("scripts/apply_postgres_migrations.py", ["--check", "--json",
      "--reader-passfile", "/run/secrets/reader_pgpass", "--writer-passfile", "/run/secrets/writer_pgpass"])).stdout);
    if (!status.applied_versions.length || !status.initialized)
      throw new Error("Existing PostgreSQL study data is not initialized. Verify the historical cutover; Tempo will not import SQLite or create an empty study database.");
    if (!status.roles_ready) throw new Error("PostgreSQL reader/writer roles or table permissions are incomplete. Inspect the maintenance role provisioning before starting Tempo.");
    if (!status.credentials_ready) throw new Error("PostgreSQL reader/writer credentials did not pass the maintenance check.");
    if (fallback && !deploymentCanStart(previous, status.applied_versions.at(-1), Object.values(images)))
      throw new Error("The previous deployment is incompatible with the current database. Preserve the backup and fix forward with compatible code.");
    return status;
  }

  async function runningServices() {
    const result = await compose(["ps", "-a", "--format", "json"]);
    const text = result.stdout.trim();
    const rows = text.startsWith("[") ? JSON.parse(text) : text.split("\n").filter(Boolean).map(line => JSON.parse(line));
    return rows.filter(row => row.State === "running").map(row => row.Service);
  }

  async function stopApplications({ preservePhase = false } = {}) {
    if (!preservePhase) stage("stopping_application_services");
    if (!stoppedServices.length) stoppedServices = await runningServices();
    const services = [...applicationServices, "postgres-backup"].filter(name => configuration.services[name]);
    await compose(["stop", "--timeout", "60", ...services], { echo: true });
  }

  const backupCommand = (args, options = {}) => compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
    "--entrypoint", "sh", "postgres-backup", "-ec", ...args], options);

  async function backup() {
    stage("verifying_backup");
    const stamp = `${new Date().toISOString().replaceAll(/[-:.]/g, "")}-${operation.id}`;
    const name = `tempo-${stamp}.dump`;
    const restoreDatabase = `tempo_cli_restore_${operation.id.replaceAll("-", "")}`;
    const user = configuration.services.postgres.environment.POSTGRES_USER ?? "postgres";
    const database = configuration.services.postgres.environment.POSTGRES_DB;
    backupRecord = { filename: name, restore_database: restoreDatabase, created_at: new Date().toISOString(), verified: false };
    operation.backup = backupRecord; atomicJson(journalPath, operation);
    await backupCommand([
      'test ! -e "/backups/$1"; pg_dump -h postgres -U "$2" -d "$3" -Fc -f "/backups/$1.partial"; pg_restore -l "/backups/$1.partial" >/dev/null; mv "/backups/$1.partial" "/backups/$1"; cd /backups; sha256sum "$1" > "$1.sha256"; sha256sum -c "$1.sha256"',
      "sh", name, user, database,
    ], { echo: true });
    await backupCommand(['createdb -h postgres -U "$1" "$2"', "sh", user, restoreDatabase]);
    backupRecord.restore_created = true; atomicJson(journalPath, operation);
    await backupCommand(['pg_restore -h postgres -U "$1" -d "$2" --no-owner --no-privileges --single-transaction "/backups/$3"',
      "sh", user, restoreDatabase, name]);
    await maintenance("scripts/verify_postgres_backup.py", [`postgresql://${user}@postgres:5432/${database}`,
      `postgresql://${user}@postgres:5432/${restoreDatabase}`], { echo: true });
    backupRecord.verified = true; atomicJson(journalPath, operation);
    await backupCommand(['dropdb -h postgres -U "$1" "$2"', "sh", user, restoreDatabase]);
    backupRecord.restore_removed = true; atomicJson(journalPath, operation);
    log(`Verified backup: ${name}`);
    return backupRecord;
  }

  async function migrate() {
    stage("applying_migrations");
    operation.study_invariants = JSON.parse((await maintenance("scripts/verify_postgres_cli_state.py")).stdout);
    atomicJson(journalPath, operation);
    await maintenance("scripts/apply_postgres_migrations.py", [], { echo: true });
    await maintenance("scripts/verify_postgres_cli_state.py", ["--expected", JSON.stringify(operation.study_invariants)]);
  }

  async function startServices({ recreate = false } = {}) {
    stage("starting_services");
    const workers = ["foreground-worker", "background-worker", "background-scheduler", "api"].filter(name => configuration.services[name]);
    await compose(["up", "-d", "--no-build", "--no-deps", ...(recreate ? ["--force-recreate"] : []), "--wait", "--wait-timeout", "180", ...workers], { echo: true });
    const consumers = ["web", "defense-engine", "maia-worker", "postgres-backup"].filter(name => configuration.services[name]);
    await compose(["up", "-d", "--no-build", "--no-deps", ...(recreate ? ["--force-recreate"] : []), "--wait", "--wait-timeout", "180", ...consumers], { echo: true });
  }

  async function verifyReady() {
    stage("checking_readiness");
    const deadline = Date.now() + readinessMilliseconds;
    let reason = "Services have not answered yet.";
    while (Date.now() < deadline) {
      try {
        const response = await fetcher(`${target.webUrl}/api/health`, { signal: AbortSignal.timeout(5000) });
        const text = await response.text();
        if (!response.ok) throw new Error(`API health HTTP ${response.status}: ${text}`);
        const health = JSON.parse(text);
        if (health.status !== "ok" || health.storage !== "postgresql" || Boolean(health.test_instance) !== Boolean(target.disposable))
          throw new Error("API health identifies a different product or test instance.");
        const web = await fetcher(target.webUrl, { signal: AbortSignal.timeout(5000) });
        if (!web.ok) throw new Error(`Web HTTP ${web.status}`);
        const running = await runningServices();
        const required = [...applicationServices, "postgres", "redis", "postgres-backup"].filter(name => configuration.services[name]);
        if (required.some(name => !running.includes(name))) throw new Error(`Required services unavailable: ${required.filter(name => !running.includes(name)).join(", ")}`);
        return;
      } catch (error) { reason = redact(error.message, secretValues); }
      await new Promise(resolveWait => setTimeout(resolveWait, 1000));
    }
    throw new Error(`Tempo did not become ready: ${reason}`);
  }

  async function commitDeployment() {
    const record = { version: 1, revision, schema: status.expected_version, images, composeFiles,
      imageOverride, configFingerprint: configurationFingerprint(configuration), evidence: evidence ?? previous?.evidence,
      verified_at: new Date().toISOString(), ...((backupRecord ?? previous?.backup) ? { backup: backupRecord ?? previous.backup } : {}) };
    if (fallback) {
      operation.phase = "ready_previous_version";
    } else {
      atomicJson(join(stateDirectory, "deployment.json"), record);
      operation.phase = "ready";
    }
    atomicJson(journalPath, operation);
    return record;
  }

  async function recordFailure(error) {
    operation.failure = redact(error.message, secretValues); operation.failed_phase = operation.phase; operation.phase = "failed";
    atomicJson(journalPath, operation);
    try { const logs = await compose(["logs", "--no-color", "--tail", "60", "api", "foreground-worker", "background-worker"], { allowFailure: true });
      writeFileSync(join(stateDirectory, `failure-${operation.id}.log`), redact(logs.stdout + logs.stderr, secretValues), { mode: 0o600 }); }
    catch { /* The original failure remains the error. */ }
  }

  async function stopAll() {
    await stopApplications();
    await compose(["stop", "--timeout", "60", "redis", "postgres"], { echo: true });
    operation.phase = "stopped"; atomicJson(journalPath, operation);
  }

  async function backupOnly() {
    const originallyRunning = await runningServices();
    await ensureImages(); await ensureDatabase(); await checkSchema();
    await stopApplications();
    let failure;
    try {
      await backup();
    } catch (error) { failure = error; await recordFailure(error); }
    try {
      if (originallyRunning.length) await compose(["up", "-d", "--no-build", "--no-deps", ...originallyRunning], { echo: true });
      const newlyStarted = ["redis", "postgres"].filter(name => !originallyRunning.includes(name));
      if (newlyStarted.length) await compose(["stop", "--timeout", "60", ...newlyStarted], { echo: true });
      if (originallyRunning.includes("web")) await verifyReady();
    } catch (error) { failure = failure ? new AggregateError([failure, error], "Backup and service restoration failed") : error; await recordFailure(failure); }
    if (failure) throw failure;
    operation.phase = "backup_verified"; atomicJson(journalPath, operation);
  }

  return { config, inspectTarget, compose, docker, runningServices, ensureImages, ensureDatabase,
    checkSchema, stopApplications, backup, migrate, startServices, verifyReady, commitDeployment,
    recordFailure, stopAll, backupOnly, secretValues, get configuration() { return configuration; } };
}
