// Bounded deployment stages. No work is scheduled from application startup.
import { createHash, randomUUID } from "node:crypto";
import { managedComposeBuild } from './development-budget.mjs';
import { closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { basename, dirname, isAbsolute, join } from "node:path";
import { isDeepStrictEqual } from 'node:util';
import {modeOverrideFor, modeRecordArgs, modes, modeStatusArgs} from './managed-games-control.mjs';
import { atomicJson, deploymentCanStart, redact, targetKey, validateContainers, validateTarget } from "./tempo-deployment.mjs";
import { TempoProblem, classifyRedisReply, phaseGuidance } from "./tempo-guidance.mjs";

import { isTimingPublicationFailure } from "./test-scenario-timings.mjs";
import { probeLifecycleRedis } from "../database/tooling/cli/probes.mjs";
import { createBackupRestoreDatabase, createPostgresBackup, removeBackupRestoreDatabase, restorePostgresBackup } from "../database/tooling/cli/backup.mjs";

export const applicationServices = ["web", "api",
  "foreground-worker", "background-worker", "background-scheduler"];

function applicationIdentitiesMatch(running, target, services, images, hashes) {
  return running.every(container => {
    const labels = container.Config?.Labels ?? {};
    const service = labels["com.docker.compose.service"];
    return labels["com.docker.compose.project"] === target.project && services.includes(service)
      && running.filter(other => other.Config?.Labels?.["com.docker.compose.service"] === service).length === 1
      && container.Image === images[service] && hashes.has(service)
      && labels["com.docker.compose.config-hash"] === hashes.get(service);
  });
}

export function configurationFingerprint(configured) {
  const stable = structuredClone(configured);
  for (const service of Object.values(stable.services)) {
    delete service.image;
    if (service.build) delete service.build.labels;
    if (service.labels) delete service.labels["org.opencontainers.image.revision"];
  }
  return createHash("sha256").update(JSON.stringify(stable)).digest("hex");
}

// Only the exact migration with a version-scoped comparator may cross this cut.
export function cut62WitnessName(guard) {
  const crossing = guard.starting_schema < 62 && guard.intended_schema >= 62;
  if (!crossing) return null;
  if (![59, 61].includes(guard.starting_schema) || guard.intended_schema !== 62 ||
      !/^[0-9a-f-]{36}$/i.test(guard.attempt_id ?? ''))
    throw new Error('Unsupported schema 62 crossing; preserve the original backup and fix forward.');
  return `cut62-${guard.attempt_id}.json`;
}

// Validate the installed ledger before any application shutdown. A schema-60
// continuation is valid only under the original pending schema-59 guard.
export function validateCut62Preflight(status, guard) {
  const current = status.applied_versions.at(-1), expected = status.expected_version;
  if (expected < 62) return;
  if (expected === 62 && current === 62) return;
  const guarded60 = current === 60 && guard?.state === "pending" && guard.starting_schema === 59 && guard.intended_schema === 62;
  if (expected !== 62 || (![59, 61].includes(current) && !guarded60))
    throw new Error(`Unsupported schema ${current}-to-${expected} crossing; applications have not been stopped. Use a supported intermediate release.`);
}

// Cold startup may bring up only the registered dependencies while no
// application process can write. Unknown or restarting project containers
// are treated as possible writers rather than assumed safe.
export function assertColdPreflightContainers(containers, project, { databaseStarted = false } = {}) {
  const allowed = new Set(databaseStarted ? ["postgres", "redis"] : ["redis"]);
  for (const container of containers) {
    const labels = container.Config?.Labels ?? {};
    if (labels["com.docker.compose.project"] !== project ||
        !(container.State?.Running || container.State?.Restarting)) continue;
    const service = labels["com.docker.compose.service"];
    if (!allowed.has(service))
      throw new Error(`Registered ${service || "unknown"} container may be active while PostgreSQL is unavailable; applications were not stopped. Inspect the registered target before retrying.`);
  }
}

export function cut62MountDirectory(stateDirectory, witnessName) {
  if (!isAbsolute(stateDirectory) || /[:,\n\r]/.test(stateDirectory))
    throw new Error('Cannot mount private migration state safely; preserve the original guard.');
  if (!/^(?:cut62|pre62)-[0-9a-f-]{36}\.json$/i.test(witnessName ?? '')) throw new Error('Invalid schema 62 witness identity.');
  return join(stateDirectory, witnessName.replace(/\.json$/, ''));
}

export function requirePrivateCut62Witness(path) {
  let info;
  try { info = lstatSync(path); } catch { throw new Error('Schema 62 original witness is missing; keep writers stopped. Preserve the guard and verified backup for manual recovery; never recapture after an interrupted attempt.'); }
  if (!info.isFile() || (info.mode & 0o077)) throw new Error('Schema 62 witness is not a private regular file; keep writers stopped.');
}

// Read-only classification shared by diagnostics and lock-time preflight.
// Authorization to retry never establishes successful history verification.
export function assessMigrationRecovery({ guard, operation, target, configuration, retry = false }) {
  if (guard) {
    const database = { name: configuration.services.postgres.environment.POSTGRES_DB,
      volume: target.volumes[target.postgresVolumeKey].name };
    if (guard.version !== 1 || guard.target !== targetKey(target)
      || guard.database?.name !== database.name || guard.database?.volume !== database.volume
      || !["pending", "verified"].includes(guard.state)
      || !Number.isInteger(guard.starting_schema) || !Number.isInteger(guard.intended_schema)
      || guard.starting_schema < 1 || guard.intended_schema < guard.starting_schema
      || !Array.isArray(guard.starting_versions) || guard.starting_versions.length !== guard.starting_schema
      || guard.starting_versions.some((version, index) => version !== index + 1) || !guard.study_invariants
      || !guard.backup?.verified || !guard.backup.filename)
      return { status: "blocked", code: "migration_guard_invalid", message: "Migration guard is invalid or belongs to another database. Preserve the guard and backup; inspect tempo doctor before recovery." };
    if (guard.state === "pending") {
      if (!retry) return { status: "blocked", code: "migration_pending", backup: guard.backup.filename, message: `Previous migration failed or was interrupted. Inspect tempo doctor and original backup ${guard.backup.filename}, fix the cause, then explicitly run tempo migrate --retry.` };
      return { status: "retry-authorized", code: "migration_retry", message: "Original-history verification remains unresolved until the real lifecycle successfully performs it." };
    }
  } else if (operation?.phase === "applying_migrations"
    || (operation?.phase === "failed" && operation.failed_phase === "applying_migrations")) {
    return { status: "blocked", code: "migration_guard_missing", message: "Previous migration failed or was interrupted without a durable original guard. Preserve the original backup and operation; inspect tempo doctor and recover its original history before tempo migrate --retry." };
  }
  return { status: "clear" };
}

export async function executeLifecycle(plan, actions) {
  // Authorization/guard failures must not replace the previous operation's
  // evidence, especially an older failed attempt without a durable guard.
  const migrationVerificationRequired = await actions.checkMigrationRetry?.();
  let interruptedApplications = false;
  try {
    const imagePreparation = await actions.ensureImages();
    await actions.preflightSchema?.();
    const allowDependencyRecreation = Boolean(plan.recreate || imagePreparation?.dependenciesMayChange);
    if (!allowDependencyRecreation && actions.recordedApplicationsMatch) {
      // An identity probe failure must also leave unconfirmed writers stopped.
      // Clear this obligation only after positively matching the saved receipt.
      interruptedApplications = true;
      interruptedApplications = !(await actions.recordedApplicationsMatch());
    }
    if (allowDependencyRecreation || interruptedApplications) {
      // Image preparation may fail without downtime. Only after it succeeds
      // may dependency changes begin, with every application consumer stopped.
      interruptedApplications = true;
      await actions.stopApplications();
    }
    await actions.ensureDatabase({ allowRecreation: allowDependencyRecreation });
    const status = await actions.checkSchema();
    if (status.pending_versions.length || migrationVerificationRequired) {
      if (!interruptedApplications) {
        interruptedApplications = true;
        await actions.stopApplications();
      }
      if (!migrationVerificationRequired) await actions.backup();
      await actions.migrate();
      const updated = await actions.checkSchema();
      if (updated.pending_versions.length) throw new Error("Migrations did not reach the required schema version.");
      await actions.resolveMigrationGuard?.();
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

// Package the optional profile in new releases, without activating it. Old frozen
// receipts remain exact and are never augmented by current checkout files.
export function releaseComposeFiles(target, { fallback = false, previous = null } = {}) {
  if (fallback) return previous.composeFiles;
  const optional = join(target.root, "docker-compose.games-feedback.yml");
  return existsSync(optional) && !target.composeFiles.includes(optional)
    ? [...target.composeFiles, optional] : target.composeFiles;
}

export function createRuntime(target, { run, stateDirectory, revision, evidence, previous = null,
  fallback = false, preparedImages = null, retry = false, log = console.log, fetcher = fetch, readinessMilliseconds = 180_000,
  redisReadinessOptions = {}, readinessOptions = {}, buildImages = managedComposeBuild }) {
  let composeFiles = releaseComposeFiles(target, { fallback, previous });
  let imageOverride = fallback ? previous.imageOverride : null;
  let modeOverride = fallback ? previous?.gamesMode?.override ?? null : null;
  let configuration;
  let containers = [];
  let images = {};
  let status;
  let backupRecord;
  let stoppedServices = [];
  const secretValues = [];
  const journalPath = join(stateDirectory, "operation.json");
  const guardPath = join(stateDirectory, "migration-guard.json");
  let migrationGuard = existsSync(guardPath) ? JSON.parse(readFileSync(guardPath, "utf8")) : null;
  let originalHistoryVerified = false;
  let verifiedPostgresImage;
  const operation = { id: randomUUID(), revision, started_at: new Date().toISOString(), phase: "checking", target: target.project };
  const docker = (args, options) => run("docker", ["--context", target.context, ...args], options);
  const composePrefix = () => ["compose", "--project-directory", target.root,
    ...(target.envFile ? ["--env-file", target.envFile] : []), "-p", target.project,
    ...composeFiles.flatMap(path => ["-f", path]), ...(imageOverride ? ["-f", imageOverride] : [])];
  const compose = (args, options) => docker([...composePrefix(), ...(modeOverride ? ['-f', modeOverride] : []), ...args], options);
  const stage = name => { operation.phase = name; atomicJson(journalPath, operation); log(`Tempo: ${name.replaceAll("_", " ")}`); };

  async function config() {
    if (modeOverride) {
      try {
        const saved = previous?.gamesMode;
        const base = JSON.parse((await docker([...composePrefix(), '--profile', 'maintenance', '--profile', 'games-feedback', 'config', '--format', 'json'])).stdout);
        if (saved?.version !== 1 || !modes.has(saved.mode) || modeOverride !== join(dirname(imageOverride), 'games-mode.json') ||
            saved.baseFingerprint !== configurationFingerprint(base) ||
            !isDeepStrictEqual(JSON.parse(readFileSync(modeOverride, 'utf8')), modeOverrideFor(base.services?.['game-feedback-worker'], saved.mode)))
          throw new Error('managed mode configuration invalid');
      } catch {
        // Discard only the invalid optional command/label overlay. Ordinary
        // services retain the frozen base configuration and exact image checks.
        modeOverride = null;
        operation.games_mode = {phase: 'inactive_unconfirmed', mode: previous?.gamesMode?.mode ?? 'collect-only'};
        log('Tempo: optional Games mode configuration is unavailable; feedback requires explicit verification.');
      }
    }
    configuration = JSON.parse((await compose(["--profile", "maintenance", "--profile", "games-feedback", "config", "--format", "json"])).stdout);
    validateTarget(configuration, target);
    for (const secret of Object.values(configuration.secrets ?? {})) {
      const text = readFileSync(secret.file, "utf8").trim();
      secretValues.push(text);
      for (const line of text.split("\n")) if (line.split(":").length >= 5) secretValues.push(line.split(":").slice(4).join(":"));
    }
    for (const service of Object.values(configuration.services)) for (const [key, value] of Object.entries(service.environment ?? {}))
      if (/TOKEN|PASSWORD|SECRET|AUTHORIZATION/i.test(key)) secretValues.push(String(value));
    return configuration;
  }

  async function inspectContainerInventory() {
    const inspectionAction = `Next: restore Docker access in context ${target.context} or let the named container operation finish, then run tempo start. Keep all study volumes.`;
    for (let inventoryAttempt = 0; inventoryAttempt < 2; inventoryAttempt++) {
      const inventoryIds = (await docker(["ps", "-aq"], { timeout: 5000 })).stdout.trim().split(/\s+/).filter(Boolean);
      if (!inventoryIds.length) return [];
      const inspection = await docker(["inspect", ...inventoryIds], { allowFailure: true, timeout: 5000 });
      if (inspection.code === 0) {
        let inspectedContainers;
        try { inspectedContainers = JSON.parse(inspection.stdout); } catch { /* Reject invalid metadata below. */ }
        if (!Array.isArray(inspectedContainers) || inspectedContainers.length !== inventoryIds.length
          || inventoryIds.some(id => !inspectedContainers.some(container => typeof container.Id === "string" && container.Id.startsWith(id))))
          throw new TempoProblem("container_inventory_invalid", "Docker container metadata is incomplete or invalid; installation safety cannot be checked.", { action: inspectionAction });
        return inspectedContainers;
      }
      const failureLines = inspection.stderr.trim().split(/\r?\n/);
      const missingIds = failureLines.map(line => line.match(/^(?:error(?: response from daemon)?:\s*)?no such (?:object|container):\s*([a-f0-9]{12,64})\s*$/i)?.[1]);
      // An auto-removed container can disappear between ps and inspect. Refresh
      // once only for that exact response, then validate every surviving owner.
      if (inventoryAttempt === 0 && inspection.code === 1 && missingIds.every(id => id && inventoryIds.includes(id))) continue;
      throw new TempoProblem("container_inventory_failed", `Docker container inspection failed: ${redact(inspection.stderr || `inspection exited ${inspection.code ?? "interrupted"} without complete metadata`, secretValues).slice(-400)}`,
        { action: inspectionAction });
    }
  }

  async function inspectTarget() {
    const daemonId = (await docker(["info", "--format", "{{.ID}}"])).stdout.trim();
    if (!target.daemonId || daemonId !== target.daemonId) throw new Error("Docker daemon differs from the registered Tempo target.");
    await config();
    containers = await inspectContainerInventory();
    validateContainers(containers, target);
    // Old registrations retain their deployment key, but the retired journal is no longer a runtime dependency.
    for (const expected of Object.values(target.volumes)) {
      if (expected.name === "tempo-engine-operations" && !target.disposable) continue;
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
    let desiredFingerprint = configurationFingerprint(configuration);
    if (!fallback && previous?.revision === revision && previous.gamesMode?.baseFingerprint === desiredFingerprint) {
      composeFiles = previous.composeFiles; imageOverride = previous.imageOverride; modeOverride = previous.gamesMode.override;
      await config(); desiredFingerprint = configurationFingerprint(configuration);
    }
    const reusable = previous && previous.revision === revision && (previous.configFingerprint === desiredFingerprint ||
      !modeOverride && previous.gamesMode?.baseFingerprint === desiredFingerprint);
    if (fallback || reusable) {
      if (previous.evidence?.commit !== revision || !previous.verified_at
        || Object.keys(configuration.services).some(name => !previous.images?.[name]))
        throw new Error("Recorded image receipt is incomplete or belongs to a different revision. Preserve it and inspect tempo doctor.");
      for (const image of Object.values(previous.images)) await docker(["image", "inspect", image]);
      images = previous.images;
      composeFiles = previous.composeFiles; imageOverride = previous.imageOverride;
      await config();
      if (Object.entries(configuration.services).some(([name, service]) => service.image !== images[name]))
        throw new Error("Saved Compose image receipt differs from its recorded immutable images.");
      await verifySelectedPostgresImage();
      return { dependenciesMayChange: !(await recordedDependenciesMatch()) };
    }
    stage("preparing_images");
    modeOverride = null;
    const releaseDirectory = join(stateDirectory, "releases", `${revision}-${operation.id}`);
    mkdirSync(releaseDirectory, { recursive: true, mode: 0o700 });
    composeFiles = releaseComposeFiles(target).map((source, index) => {
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
      await config(); await verifySelectedPostgresImage(); return { dependenciesMayChange: true };
    }
    const labelsFile = join(releaseDirectory, "build-labels.json");
    const buildServices = Object.entries(configuration.services).filter(([, service]) => service.build).map(([name]) => name);
    atomicJson(labelsFile, { services: Object.fromEntries(buildServices.map(name => [name,
      { image: `${target.project}-${name}:tempo-${revision}`, build: { labels: { "org.opencontainers.image.revision": revision } } }])) });
    composeFiles.push(labelsFile);
    await buildImages(async () => (await compose(["--profile", "maintenance", "build", "--print", ...buildServices])).stdout,
      args => docker(args, {echo:true}), { docker: async args => (await docker(args)).stdout });
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
    await verifySelectedPostgresImage();
    return { dependenciesMayChange: true };
  }

  async function verifySelectedPostgresImage() {
    if (verifiedPostgresImage && verifiedPostgresImage === images.postgres) return;
    await verifyPostgresImageMajor(docker, images.postgres, target.postgresMajor);
    verifiedPostgresImage = images.postgres;
  }

  async function recordedDependenciesMatch() {
    // Resolve hashes from the saved Compose definitions and immutable image
    // overlay, rather than trusting tags or a receipt's desired state alone.
    const hashes = new Map((await compose(["--profile", "maintenance", "config", "--hash", "*"])).stdout.trim()
      .split("\n").filter(Boolean).map(line => line.trim().split(/\s+/)));
    const ids = (await compose(["ps", "-aq", "postgres", "redis"])).stdout.trim().split(/\s+/).filter(Boolean);
    const dependencies = ids.length ? JSON.parse((await docker(["inspect", ...ids])).stdout) : [];
    validateContainers(dependencies, target);
    return ["postgres", "redis"].every(service => {
      const matches = dependencies.filter(container => container.Config?.Labels?.["com.docker.compose.project"] === target.project
        && container.Config?.Labels?.["com.docker.compose.service"] === service);
      return hashes.has(service) && matches.length === 1 && matches[0].Image === images[service]
        && matches[0].Config.Labels["com.docker.compose.config-hash"] === hashes.get(service);
    });
  }

  async function recordedApplicationsMatch() {
    // ensureImages selected the saved definitions and immutable overlay on the
    // reusable/fallback path. A stale journal alone says nothing about what is
    // running; inspect the actual application layer before schema rejection.
    const services = [...applicationServices, "postgres-backup"].filter(name => configuration.services[name]);
    const hashes = new Map((await compose(["--profile", "maintenance", "config", "--hash", "*"])).stdout.trim()
      .split("\n").filter(Boolean).map(line => line.trim().split(/\s+/)));
    const ids = (await compose(["ps", "-aq", ...services])).stdout.trim().split(/\s+/).filter(Boolean);
    const applications = ids.length ? JSON.parse((await docker(["inspect", ...ids])).stdout) : [];
    validateContainers(applications, target);
    const running = applications.filter(container => container.State?.Running);
    return applicationIdentitiesMatch(running, target, services, images, hashes);
  }

  async function inspectRunningRevision() {
    const running = containers.filter(container => container.State?.Running
      && container.Config?.Labels?.["com.docker.compose.project"] === target.project
      && applicationServices.includes(container.Config?.Labels?.["com.docker.compose.service"]));
    if (!running.length) return { status: "unknown", detail: "no running application services", receipt: "unverified" };
    const missingServices = applicationServices.filter(service => configuration.services[service]
      && !running.some(container => container.Config?.Labels?.["com.docker.compose.service"] === service));
    let imageDetails = [];
    try {
      const inspection = await docker(["image", "inspect", ...new Set(running.map(container => container.Image))],
        { allowFailure: true, timeout: 5000 });
      const returnedImages = JSON.parse(inspection.stdout);
      if (Array.isArray(returnedImages)) imageDetails = returnedImages.filter(image => image && typeof image.Id === "string");
    } catch { /* Container IDs/configuration can still establish receipt identity. */ }
    const unavailableImageServices = running.filter(container => !imageDetails.some(image => image.Id === container.Image))
      .map(container => container.Config?.Labels?.["com.docker.compose.service"]);
    const revisions = running.map(container => imageDetails.find(image => image.Id === container.Image)
      ?.Config?.Labels?.["org.opencontainers.image.revision"]);
    const knownRevisions = [...new Set(revisions.filter(value => /^[a-f0-9]{40}$/.test(value ?? "")))];
    const duplicateService = running.some(container => running.filter(other => other.Config?.Labels?.["com.docker.compose.service"]
      === container.Config?.Labels?.["com.docker.compose.service"]).length !== 1);
    const identity = knownRevisions.length > 1 || duplicateService
      ? { status: "mixed", detail: knownRevisions.join(", ") || "duplicate application services" }
      : revisions.some(value => !/^[a-f0-9]{40}$/.test(value ?? ""))
        ? { status: "unknown", detail: unavailableImageServices.length ? "immutable image inspection unavailable" : "revision labels missing from immutable images" }
        : { status: "consistent", detail: `${knownRevisions[0]} across ${running.length} inspected running services` };
    if (identity.status === "unknown" && knownRevisions.length)
      identity.detail += `; available revision evidence (incomplete): ${knownRevisions.join(", ")}`;
    if (unavailableImageServices.length) identity.detail += `; immutable image inspection unavailable for: ${unavailableImageServices.join(", ")}`;
    if (missingServices.length) {
      if (identity.status === "consistent") identity.status = "partial";
      identity.detail += `; missing services: ${missingServices.join(", ")}`;
    }
    identity.receipt = "unverified";
    if (previous?.evidence?.commit === previous?.revision && previous?.verified_at && previous?.images) {
      try {
        const hashes = new Map((await compose(["--profile", "maintenance", "config", "--hash", "*"], { timeout: 5000 })).stdout.trim()
          .split("\n").filter(Boolean).map(line => line.trim().split(/\s+/)));
        const services = [...applicationServices, "postgres-backup"].filter(name => configuration.services[name]);
        const inspected = containers.filter(container => container.State?.Running
          && container.Config?.Labels?.["com.docker.compose.project"] === target.project
          && services.includes(container.Config?.Labels?.["com.docker.compose.service"]));
        const revisionContradiction = knownRevisions.some(imageRevision => imageRevision !== previous.revision);
        identity.receipt = !revisionContradiction && applicationIdentitiesMatch(inspected, target, services, previous.images, hashes)
          ? "matches immutable images and configuration of inspected running services" : "differs from running service identities";
      } catch { identity.receipt = "unverified (saved configuration inspection unavailable)"; }
    }
    return identity;
  }

  async function ensureDatabase({ allowRecreation = false } = {}) {
    await verifySelectedPostgresImage();
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
    await compose(["up", "-d", "--no-build", "--no-deps", ...(allowRecreation ? ["--force-recreate"] : ["--no-recreate"]),
      "postgres", "redis"], { echo: true });
    // Probe immediately: Compose can exhaust its health retries before our
    // deadline, or conceal an authentication error behind an unhealthy state.
    await waitForRedisReady();
    stage("checking_database");
    await compose(["up", "-d", "--no-build", "--no-deps", "--no-recreate", "--wait", "--wait-timeout", "180", "postgres"], { echo: true });
  }

  async function waitForRedisReady() {
    stage("checking_redis");
    const { timeoutMilliseconds = 180_000, now = () => performance.now(),
      wait = milliseconds => new Promise(resolveWait => setTimeout(resolveWait, milliseconds)), poll = () => {} } = redisReadinessOptions;
    const started = now();
    const deadline = started + timeoutMilliseconds;
    let reason = "No Redis readiness reply was received";
    let reportedWait = false;
    const fail = () => {
      throw new Error(`Redis is not ready after ${((now() - started) / 1000).toFixed(1)} seconds (checking_redis): ${reason}. Inspect tempo logs redis, then retry tempo start.`);
    };
    while (now() < deadline) {
      poll();
      let interrupted = false;
      try {
        const response = await probeLifecycleRedis(compose,
          { allowFailure: true, timeout: Math.min(5000, Math.max(1, Math.ceil(deadline - now()))) });
        if (response.code === 0 && response.stdout.trim() === "PONG" && now() <= deadline) return;
        reason = redact(response.stdout.trim() || response.stderr.trim() || "Redis readiness probe was interrupted or timed out", secretValues).slice(-4000);
        interrupted = response.code === null;
      } catch (error) {
        if (isTimingPublicationFailure(error)) throw error;
        reason = redact(error.message, secretValues).slice(-4000);
      }
      // Saved deployments can contain the old healthcheck, which accepts
      // LOADING with exit zero. Only a real PONG permits maintenance to advance.
      const reply = classifyRedisReply({ code: 1, stderr: reason, interrupted });
      if (reply.status !== "transient" || now() >= deadline) fail();
      if (!reportedWait) { log(`Tempo: waiting for Redis: ${reason}`); reportedWait = true; }
      await wait(Math.min(1000, deadline - now()));
    }
    fail();
  }

  async function maintenance(script, args = [], options = {}) {
    const { cut62Mount = null, ...runOptions } = options;
    const mountDirectory = cut62Mount ? cut62MountDirectory(stateDirectory, cut62Mount) : null;
    return compose(["--profile", "maintenance", "run", "--rm", "--no-deps", "-T",
      ...(mountDirectory ? ["--volume", `${mountDirectory}:/cut62`] : []),
      "migration", script, ...args], runOptions);
  }

  async function checkSchema() {
    stage("checking_schema");
    status = JSON.parse((await maintenance("database/tooling/apply_postgres_migrations.py", ["--check", "--json",
      "--reader-passfile", "/run/secrets/reader_pgpass", "--writer-passfile", "/run/secrets/writer_pgpass"])).stdout);
    if (!status.applied_versions.length || !status.initialized)
      throw new Error("Existing PostgreSQL study data is not initialized. Verify the historical cutover; Tempo will not import SQLite or create an empty study database.");
    if (!status.roles_ready) throw new Error("PostgreSQL reader/writer roles or table permissions are incomplete. Inspect the maintenance role provisioning before starting Tempo.");
    if (!status.credentials_ready) throw new Error("PostgreSQL reader/writer credentials did not pass the maintenance check.");
    if (fallback && !deploymentCanStart(previous, status.applied_versions.at(-1), Object.values(images)))
      throw new Error("The previous deployment is incompatible with the current database. Preserve the backup and fix forward with compatible code.");
    if (migrationGuard?.state === "pending") {
      const currentSchema = status.applied_versions.at(-1);
      if (status.expected_version < migrationGuard.intended_schema || currentSchema < migrationGuard.starting_schema
        || currentSchema > status.expected_version
        || migrationGuard.starting_versions.some((version, index) => status.applied_versions[index] !== version))
        throw new Error(`Candidate schema/ledger cannot safely continue the original migration. Preserve backup ${migrationGuard.backup.filename} and fix forward with compatible code.`);
    }
    validateCut62Preflight(status, migrationGuard);
    return status;
  }

  async function preflightSchema() {
    if (!(await runningServices()).includes("postgres")) {
      const before = await inspectContainerInventory();
      validateContainers(before, target);
      assertColdPreflightContainers(before, target.project);
      // Existing cluster identity is checked read-only by ensureDatabase. It
      // starts only PostgreSQL/Redis with --no-recreate, never application workers.
      await ensureDatabase({ allowRecreation: false });
      const after = await inspectContainerInventory();
      validateContainers(after, target);
      assertColdPreflightContainers(after, target.project, { databaseStarted: true });
    }
    return checkSchema();
  }

  function checkMigrationRetry() {
    // Read under the maintenance lock, including the backup path whose runtime
    // was created during the earlier read-only inspection.
    migrationGuard = existsSync(guardPath) ? JSON.parse(readFileSync(guardPath, "utf8")) : null;
    const previousOperation = !migrationGuard && existsSync(journalPath) ? JSON.parse(readFileSync(journalPath, "utf8")) : null;
    const assessment = assessMigrationRecovery({ guard: migrationGuard, operation: previousOperation, target, configuration, retry });
    if (assessment.status === "blocked") throw new Error(assessment.message);
    return assessment.status === "retry-authorized";
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
    if (previous?.schema >= 56 && configuration.services['game-feedback-worker']) {
      try {
        const result = JSON.parse((await compose(modeStatusArgs('legacy'), {timeout: 5000})).stdout);
        if (result.schema_version !== 1 || result.read_only !== true || !Object.hasOwn(result, 'intent'))
          throw new Error('managed mode status unavailable');
        const intent = result.intent;
        if (intent && intent.phase !== 'inactive') {
          const next = {...intent, generation: intent.generation + 1, phase: 'inactive', operation_id: operation.id,
            runtimes: [], verified_at: null, updated_at: new Date().toISOString()};
          const saved = JSON.parse((await compose(modeRecordArgs(next, intent.generation), {timeout: 5000})).stdout);
          if (saved.applied !== true || saved.generation !== next.generation || saved.phase !== 'inactive')
            throw new Error('managed mode inactive acknowledgment unavailable');
        }
        operation.games_mode = {phase: 'inactive', mode: intent?.mode ?? previous.gamesMode?.mode ?? 'collect-only'};
      } catch {
        // Optional control failure cannot stop Study shutdown/startup. Every
        // backend container start refreshes its boot nonce, so a stale intent
        // cannot reauthorize a restarted ordinary command worker.
        operation.games_mode = {phase: 'inactive_unconfirmed', mode: previous.gamesMode?.mode ?? 'collect-only'};
        log('Tempo: optional feedback admission could not be confirmed inactive; new container boots require explicit mode verification.');
      }
      atomicJson(journalPath, operation);
    }
    // An explicitly enabled collector must not write through release maintenance.
    // It stays off afterward; startServices never starts this optional profile.
    const services = [...applicationServices, "postgres-backup", "game-feedback-worker"].filter(name => configuration.services[name]);
    await compose(["stop", "--timeout", "60", ...services], { echo: true });
  }

  async function backup() {
    stage("verifying_backup");
    const stamp = `${new Date().toISOString().replaceAll(/[-:.]/g, "")}-${operation.id}`;
    const name = `tempo-${stamp}.dump`;
    const restoreDatabase = `tempo_cli_restore_${operation.id.replaceAll("-", "")}`;
    const user = configuration.services.postgres.environment.POSTGRES_USER ?? "postgres";
    const database = configuration.services.postgres.environment.POSTGRES_DB;
    backupRecord = { filename: name, restore_database: restoreDatabase, created_at: new Date().toISOString(), verified: false };
    operation.backup = backupRecord; atomicJson(journalPath, operation);
    await createPostgresBackup(compose, name, user, database);
    await createBackupRestoreDatabase(compose, user, restoreDatabase);
    backupRecord.restore_created = true; atomicJson(journalPath, operation);
    await restorePostgresBackup(compose, user, restoreDatabase, name);
    await maintenance("database/tooling/verify_postgres_backup.py", [`postgresql://${user}@postgres:5432/${database}`,
      `postgresql://${user}@postgres:5432/${restoreDatabase}`], { echo: true });
    backupRecord.verified = true; atomicJson(journalPath, operation);
    await removeBackupRestoreDatabase(compose, user, restoreDatabase);
    backupRecord.restore_removed = true; atomicJson(journalPath, operation);
    log(`Verified backup: ${name}`);
    return backupRecord;
  }

  async function migrate() {
    originalHistoryVerified = false;
    stage("applying_migrations");
    const newGuard = migrationGuard?.state !== "pending";
    if (newGuard) {
      if (!backupRecord?.verified) throw new Error("Migration requires a restore-verified backup before capturing history.");
      // H0 belongs to this database attempt, not to a process or revision.
      // Persist it before any migration transaction can commit. Retries never
      // replace it, including when the ledger already reached the target.
      migrationGuard = { version: 1, target: targetKey(target),
        database: { name: configuration.services.postgres.environment.POSTGRES_DB, volume: target.volumes[target.postgresVolumeKey].name },
        attempt_id: operation.id, origin_revision: revision, starting_schema: status.applied_versions.at(-1),
        starting_versions: status.applied_versions, intended_schema: status.expected_version,
        study_invariants: JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py")).stdout),
        backup: backupRecord, state: "pending", created_at: new Date().toISOString() };
    }
    const witnessName = cut62WitnessName(migrationGuard);
    if (witnessName && migrationGuard.cut62_witness !== undefined && migrationGuard.cut62_witness !== witnessName)
      throw new Error('Schema 62 witness identity changed; preserve the original guard.');
    // Persisting this pending identity before capture deliberately fences a crash.
    // A missing witness requires manual recovery from the original guard/verified
    // backup; retry must never infer that recapture is safe.
    if (witnessName && newGuard) migrationGuard.cut62_witness = witnessName;
    if (witnessName && !newGuard && migrationGuard.cut62_witness !== witnessName)
      throw new Error('Schema 62 original witness is missing from the guard; keep writers stopped.');
    migrationGuard.last_attempted_revision = revision;
    migrationGuard.last_attempted_at = new Date().toISOString();
    atomicJson(guardPath, migrationGuard);
    operation.study_invariants = migrationGuard.study_invariants;
    operation.backup = migrationGuard.backup;
    atomicJson(journalPath, operation);
    if (witnessName) {
      if (migrationGuard.starting_schema === 59) {
        let current = status.applied_versions.at(-1);
        const pre62Name = `pre62-${migrationGuard.attempt_id}.json`;
        if (newGuard) {
          migrationGuard.pre62_witness = pre62Name;
          atomicJson(guardPath, migrationGuard);
        } else if (migrationGuard.pre62_witness !== pre62Name) {
          throw new Error("Original schema 59 witness identity is missing; keep writers stopped.");
        }
        const stateInfo = lstatSync(stateDirectory);
        if (!stateInfo.isDirectory() || (stateInfo.mode & 0o077))
          throw new Error("Schema 59 migration state must be a private directory; keep writers stopped.");
        const pre62Directory = cut62MountDirectory(stateDirectory, pre62Name);
        const pre62Path = join(pre62Directory, pre62Name);
        if (newGuard) {
          if (existsSync(pre62Directory)) throw new Error("Schema 59 witness directory already exists; refusing to recapture.");
          mkdirSync(pre62Directory, { mode: 0o700 });
          const captured59 = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py", ["--pre62-capture59", `/cut62/${pre62Name}`], { cut62Mount: pre62Name })).stdout);
          if (typeof captured59.witness_id !== "string" || !captured59.witness_id) throw new Error("Schema 59 witness identity is invalid; keep writers stopped.");
          for (const path of [pre62Directory, stateDirectory]) {
            const fd = openSync(path, "r");
            try { fsyncSync(fd); } finally { closeSync(fd); }
          }
          migrationGuard.pre62_witness_id = captured59.witness_id;
          atomicJson(guardPath, migrationGuard);
        }
        requirePrivateCut62Witness(pre62Path);
        if (typeof migrationGuard.pre62_witness_id !== "string" || !migrationGuard.pre62_witness_id)
          throw new Error("Original schema 59 witness identity is missing; keep writers stopped.");
        const check59 = async schema => {
          const flag = schema === 62 ? "--pre62-authenticate59" : "--pre62-verify59";
          const result = JSON.parse((await maintenance("database/tooling/verify_postgres_cli_state.py", [flag, `/cut62/${pre62Name}`], { cut62Mount: pre62Name })).stdout);
          if (result.witness_id !== migrationGuard.pre62_witness_id || (schema !== 62 && result.observed_schema !== schema))
            throw new Error("Original schema 59 witness verification differs; keep writers stopped.");
        };
        await check59(current);
        if (current < 61) {
          if (migrationGuard.cut62_intermediate_verified) throw new Error("Schema 61 checkpoint regressed; keep writers stopped.");
          await maintenance("database/tooling/apply_postgres_migrations.py", ["--target-version", "61"], { echo: true });
          const intermediate = await checkSchema();
          current = intermediate.applied_versions.at(-1);
          if (current !== 61) throw new Error("Intermediate migrations did not reach exact schema 61; keep writers stopped.");
          await check59(61);
        }
        if (!migrationGuard.cut62_intermediate_verified) {
          if (current !== 61) throw new Error("Original schema 61 checkpoint evidence is missing; keep writers stopped.");
          await maintenance("database/tooling/verify_postgres_cli_state.py", ["--expected", JSON.stringify(migrationGuard.study_invariants)]);
          migrationGuard.cut62_intermediate_verified = { schema: 61, pre62_witness: pre62Name,
            pre62_witness_id: migrationGuard.pre62_witness_id, attempt_id: migrationGuard.attempt_id,
            backup_filename: migrationGuard.backup.filename, verified_at: new Date().toISOString() };
          atomicJson(guardPath, migrationGuard);
        } else if (migrationGuard.cut62_intermediate_verified.schema !== 61 ||
          migrationGuard.cut62_intermediate_verified.pre62_witness !== pre62Name ||
          migrationGuard.cut62_intermediate_verified.pre62_witness_id !== migrationGuard.pre62_witness_id ||
          migrationGuard.cut62_intermediate_verified.backup_filename !== migrationGuard.backup.filename ||
          migrationGuard.cut62_intermediate_verified.attempt_id !== migrationGuard.attempt_id) {
          throw new Error("Invalid schema 61 checkpoint evidence; keep writers stopped.");
        }
      }
      const directory = lstatSync(stateDirectory);
      if (!directory.isDirectory() || (directory.mode & 0o077))
        throw new Error('Schema 62 migration state must be a private directory; keep writers stopped.');
      const witnessDirectory = cut62MountDirectory(stateDirectory, witnessName);
      const witnessPath = join(witnessDirectory, witnessName);
      const firstCapture = newGuard || (migrationGuard.starting_schema === 59 &&
        status.applied_versions.at(-1) === 61 && migrationGuard.cut62_intermediate_verified?.schema === 61 &&
        !migrationGuard.cut62_capture_started);
      if (firstCapture) {
        if (existsSync(witnessDirectory)) throw new Error('Schema 62 witness directory already exists; refusing to recapture.');
        migrationGuard.cut62_capture_started = true;
        atomicJson(guardPath, migrationGuard);
        mkdirSync(witnessDirectory, { mode: 0o700 });
      }
      let witnessDirectoryStat;
      try { witnessDirectoryStat = lstatSync(witnessDirectory); } catch {
        throw new Error('Schema 62 witness directory is missing; keep writers stopped. Preserve the original guard and backup for manual recovery.');
      }
      if (!witnessDirectoryStat.isDirectory() || (witnessDirectoryStat.mode & 0o077))
        throw new Error('Schema 62 witness directory must be private; keep writers stopped.');
      if (firstCapture) {
        if (existsSync(witnessPath)) throw new Error('Schema 62 witness already exists; refusing to recapture.');
        await maintenance("database/tooling/verify_postgres_cli_state.py", ["--cut62-capture", `/cut62/${witnessName}`], { cut62Mount: witnessName });
        const directoryFd = openSync(witnessDirectory, "r");
        try { fsyncSync(directoryFd); } finally { closeSync(directoryFd); }
        // Persist the new directory entry as well as its contents before migration.
        const stateDirectoryFd = openSync(stateDirectory, "r");
        try { fsyncSync(stateDirectoryFd); } finally { closeSync(stateDirectoryFd); }
      }
      requirePrivateCut62Witness(witnessPath);
      if (status.applied_versions.at(-1) === 61)
        await maintenance("database/tooling/verify_postgres_cli_state.py",
          ["--cut62-check-captured", `/cut62/${witnessName}`], { cut62Mount: witnessName });
    }
    if (status.pending_versions.length) await maintenance("database/tooling/apply_postgres_migrations.py", [], { echo: true });
    try {
      if (witnessName) await maintenance("database/tooling/verify_postgres_cli_state.py",
        ["--cut62-verify", `/cut62/${witnessName}`], { cut62Mount: witnessName });
      else await maintenance("database/tooling/verify_postgres_cli_state.py", ["--expected", JSON.stringify(migrationGuard.study_invariants)]);
      originalHistoryVerified = true;
    } catch (error) {
      throw new Error(`Original migration history verification failed; keep writers stopped. Preserve backup ${migrationGuard.backup.filename} and guard ${guardPath}. ${error.message}`, { cause: error });
    }
  }

  function resolveMigrationGuard() {
    if (migrationGuard?.state !== "pending" || !originalHistoryVerified || status.pending_versions.length
      || status.applied_versions.at(-1) !== status.expected_version)
      throw new Error("Original migration verification is unresolved; keep writers stopped.");
    migrationGuard.state = "verified";
    migrationGuard.verified_at = new Date().toISOString();
    migrationGuard.verified_schema = status.expected_version;
    atomicJson(guardPath, migrationGuard);
    backupRecord = migrationGuard.backup;
  }

  function requireVerifiedMigration() {
    if (migrationGuard?.state === "pending") throw new Error("Original migration verification is unresolved; keep writers stopped.");
  }

  async function startServices({ recreate = false } = {}) {
    requireVerifiedMigration();
    stage("starting_services");
    const workers = ["foreground-worker", "background-worker", "background-scheduler", "api"].filter(name => configuration.services[name]);
    await compose(["up", "-d", "--no-build", "--no-deps", ...(recreate ? ["--force-recreate"] : []), "--wait", "--wait-timeout", "180", ...workers], { echo: true });
    const consumers = ["web", "postgres-backup"].filter(name => configuration.services[name]);
    await compose(["up", "-d", "--no-build", "--no-deps", ...(recreate ? ["--force-recreate"] : []), "--wait", "--wait-timeout", "180", ...consumers], { echo: true });
  }

  async function verifyReady() {
    stage("checking_readiness");
    const deadline = Date.now() + readinessMilliseconds;
    let reason = "Services have not answered yet.";
    const { poll = () => {}, wait = milliseconds => new Promise(resolveWait => setTimeout(resolveWait, milliseconds)) } = readinessOptions;
    while (Date.now() < deadline) {
      poll();
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
      } catch (error) {
        if (isTimingPublicationFailure(error)) throw error;
        reason = redact(error.message, secretValues);
      }
      await wait(1000);
    }
    throw new Error(`Tempo did not become ready: ${reason}`);
  }

  async function commitDeployment() {
    requireVerifiedMigration();
    const record = { version: 1, revision, schema: status.expected_version, images, composeFiles,
      imageOverride, configFingerprint: configurationFingerprint(configuration), evidence: evidence ?? previous?.evidence,
      ...(modeOverride ? {gamesMode: previous.gamesMode} : {}),
      ...(operation.games_mode ? {gamesModeState: operation.games_mode} : {}),
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
    operation.failed_at = new Date().toISOString();
    const guidance = phaseGuidance(operation.failed_phase);
    operation.failure_log = join(stateDirectory, `failure-${operation.id}.log`);
    operation.next_action = error.action ?? guidance.action;
    atomicJson(journalPath, operation);
    try {
      const services = guidance.services.filter(service => configuration?.services?.[service]);
      const logs = services.length ? await compose(["--profile", "maintenance", "logs", "--no-color", "--tail", "60", ...services], { allowFailure: true }) : { stdout: "", stderr: "" };
      writeFileSync(operation.failure_log, redact(`Phase: ${operation.failed_phase}\nRevision: ${revision}\nFailed at: ${operation.failed_at}\n${error.message}\n${logs.stdout}${logs.stderr}`, secretValues), { mode: 0o600 });
      log(`Failure evidence: ${operation.failure_log}`);
    }
    catch { /* The original failure remains the error. */ }
    error.action ??= guidance.action;
  }

  async function stopAll() {
    await stopApplications();
    await compose(["stop", "--timeout", "60", "redis", "postgres"], { echo: true });
    operation.phase = "stopped"; atomicJson(journalPath, operation);
  }

  async function backupOnly() {
    if (checkMigrationRetry()) throw new Error("Original migration verification is unresolved. Inspect tempo doctor, then run tempo migrate --retry before taking another backup.");
    const originallyRunning = await runningServices();
    const imagePreparation = await ensureImages();
    if (imagePreparation.dependenciesMayChange) {
      const error = new Error("Live PostgreSQL/Redis differ from the recorded deployment. Run tempo start to safely reconcile dependencies before taking a backup.");
      await recordFailure(error);
      throw error;
    }
    let failure, failedPhase;
    try {
      await ensureDatabase(); await checkSchema();
      await stopApplications();
      await backup();
    } catch (error) { failure = error; failedPhase = operation.phase; }
    try {
      if (originallyRunning.length) await compose(["up", "-d", "--no-build", "--no-deps", "--no-recreate", ...originallyRunning], { echo: true });
      const newlyStarted = ["redis", "postgres"].filter(name => !originallyRunning.includes(name));
      if (newlyStarted.length) await compose(["stop", "--timeout", "60", ...newlyStarted], { echo: true });
      if (originallyRunning.includes("web")) await verifyReady();
    } catch (error) { failure = failure ? new AggregateError([failure, error], "Backup and service restoration failed") : error; }
    if (failure) { if (failedPhase) operation.phase = failedPhase; await recordFailure(failure); throw failure; }
    operation.phase = "backup_verified"; atomicJson(journalPath, operation);
  }

  return { config, inspectTarget, compose, docker, runningServices, inspectRunningRevision, ensureImages, recordedApplicationsMatch, ensureDatabase,
    checkSchema, preflightSchema, checkMigrationRetry, resolveMigrationGuard, stopApplications, backup, migrate, startServices, verifyReady, commitDeployment,
    recordFailure, stopAll, backupOnly, secretValues, get configuration() { return configuration; } };
}

async function verifyPostgresImageMajor(docker, image, registeredMajor) {
  if (!image?.startsWith("sha256:")) throw new Error("PostgreSQL version verification requires the selected immutable image ID.");
  const result = await docker(["run", "--rm", "--read-only", "--network", "none", "--entrypoint", "postgres", image, "--version"]);
  const actualMajor = Number(result.stdout.trim().match(/^postgres \(PostgreSQL\) (\d+)(?:\.|\s|$)/)?.[1]);
  if (!Number.isInteger(actualMajor) || actualMajor < 1) throw new Error(`Could not determine PostgreSQL server major from selected image ${image}.`);
  if (actualMajor !== registeredMajor)
    throw new TempoProblem("postgres_major", `Registered PostgreSQL major ${registeredMajor}; candidate image PostgreSQL major ${actualMajor}. A separate major-upgrade procedure is required.`,
      { action: "Next: follow docs/POSTGRES-MAINTENANCE.md for a separate PostgreSQL major upgrade. Do not recreate this cluster with a different server major." });
}
