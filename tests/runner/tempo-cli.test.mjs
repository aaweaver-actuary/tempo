import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { schemaVersionFromSource, validateTarget, validateContainers, deploymentCanStart,
  acquireTargetLock, qualityEvidence, atomicJson, productVolumes } from "../../scripts/tempo-deployment.mjs";
import { executeLifecycle } from "../../scripts/tempo-runtime.mjs";
import { cliFixture } from "./tempo-cli-fixture.mjs";

function directory(t) {
  const path = mkdtempSync(join(tmpdir(), "tempo-cli-regression-"));
  t.after(() => rmSync(path, { recursive: true, force: true }));
  return path;
}

function fixture(t) {
  const path = directory(t);
  mkdirSync(join(path, "checkout"));
  const secret = join(path, "password");
  writeFileSync(secret, "canary-password", { mode: 0o600 });
  const target = { root: join(path, "checkout"), project: "tempo", context: "desktop-linux",
    volumes: structuredClone(productVolumes),
    postgresVolumeKey: "tempo-postgres-data", ports: [{ service: "web", host: "127.0.0.1", port: "3000", target: 80 }] };
  const config = { name: "tempo", volumes: target.volumes, secrets: { password: { file: secret } },
    services: { postgres: { image: "postgres:18.6-trixie", environment: { POSTGRES_DB: "tempo" },
      volumes: [{ type: "volume", source: "tempo-postgres-data", target: "/var/lib/postgresql" }] }, web: { ports: [{ host_ip: "127.0.0.1", published: "3000", target: 80 }] },
      redis: { volumes: [{ type: "volume", source: "tempo-redis-data", target: "/data" }] },
      "postgres-backup": { volumes: [{ type: "volume", source: "tempo-postgres-backups", target: "/backups" }], tmpfs: ["/var/lib/postgresql"] },
      "defense-engine": { volumes: [{ type: "volume", source: "tempo-engine-operations", target: "/state" }] },
      api: { environment: { TEMPO_DATABASE_READ_URL: "postgresql://tempo_reader@postgres:5432/tempo" } } } };
  return { path, secret, target, config };
}

test("restart compares numeric PostgreSQL schema versions rather than Python source or psql formatting", () => {
  assert.equal(schemaVersionFromSource('"""Schema."""\nPOSTGRES_SCHEMA_VERSION = 29\n'), 29);
  assert.throws(() => schemaVersionFromSource("POSTGRES_SCHEMA_VERSION = invalid"), /schema version/);
});

test("CLI rejects mismatched projects volumes ports and writable reader credentials before maintenance", t => {
  const { target, config } = fixture(t);
  assert.doesNotThrow(() => validateTarget(config, target));
  for (const alter of [c => { c.name = "another-task"; },
    c => { c.volumes["tempo-postgres-data"].name = "another-database"; },
    c => { c.services.web.ports[0].host_ip = "0.0.0.0"; },
    c => { c.services.postgres.volumes[0].target = "/wrong-data"; },
    c => { c.services.api.environment.TEMPO_DATABASE_WRITE_URL = "postgresql://tempo@postgres/tempo"; }]) {
    const changed = structuredClone(config); alter(changed);
    assert.throws(() => validateTarget(changed, target), /target|volume|port|reader/i);
  }
});

test("CLI refuses missing insecure or checkout-local secret files without exposing their values", t => {
  const { target, config, secret } = fixture(t);
  config.secrets.password.file = join(target.root, "secret");
  assert.throws(() => validateTarget(config, target), /secret/i);
  config.secrets.password.file = secret;
  writeFileSync(secret, "canary-password", { mode: 0o644 });
  // Existing files retain their original mode; use an insecure separate file.
  config.secrets.password.file = join(directory(t), "insecure");
  writeFileSync(config.secrets.password.file, "canary-password", { mode: 0o644 });
  assert.throws(() => validateTarget(config, target), error => /permission/.test(error.message) && !error.message.includes("canary-password"));
});

test("CLI rejects a foreign container attached to the registered PostgreSQL volume", t => {
  const { target } = fixture(t);
  const container = { Id: "foreign", Config: { Labels: { "com.docker.compose.project": "other-task" } },
    Mounts: [{ Type: "volume", Name: "tempo-postgres-data" }] };
  assert.throws(() => validateContainers([container], target), /another|foreign|other-task/);
});

test("CLI recognizes the old backup image's unused anonymous scratch volume but rejects unknown study mounts", t => {
  const { target } = fixture(t);
  const container = { Config: { Labels: { "com.docker.compose.project": target.project,
    "com.docker.compose.service": "postgres-backup" } },
    Mounts: [{ Type: "volume", Name: "a".repeat(64), Destination: "/var/lib/postgresql" }] };
  assert.doesNotThrow(() => validateContainers([container], target));
  container.Mounts[0].Name = "unknown-study-data";
  assert.throws(() => validateContainers([container], target), /unregistered/);
  container.Mounts[0].Name = "a".repeat(64);
  container.Config.Labels["com.docker.compose.service"] = "postgres";
  assert.throws(() => validateContainers([container], target), /unregistered/);
});

test("blocked updates can start only recorded immutable images with the same database schema", () => {
  const record = { schema: 29, images: { api: "sha256:verified" }, revision: "a".repeat(40) };
  assert.equal(deploymentCanStart(record, 29, ["sha256:verified"]), true);
  assert.equal(deploymentCanStart(record, 30, ["sha256:verified"]), false);
  assert.equal(deploymentCanStart(record, 29, []), false);
  assert.equal(deploymentCanStart(null, 29, []), false);
});

test("release evidence requires the exact main revision and successful complete quality jobs", () => {
  const sha = "a".repeat(40);
  const run = { id: 12, head_sha: sha, head_branch: "main", event: "push", status: "completed", html_url: "https://github.com/example/actions/runs/12" };
  const jobs = ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, conclusion: "success", status: "completed" }));
  assert.equal(qualityEvidence(sha, run, jobs).commit, sha);
  assert.throws(() => qualityEvidence("b".repeat(40), run, jobs), /revision/);
  assert.throws(() => qualityEvidence(sha, run, jobs.filter(job => job.name !== "browser / verify")), /browser/);
  assert.throws(() => qualityEvidence(sha, run, jobs.map(job => job.name === "quality" ? { ...job, conclusion: "failure" } : job)), /quality/);
});

test("target maintenance lock prevents concurrent commands and recovers a dead owner without deleting another lock", t => {
  const path = directory(t);
  const release = acquireTargetLock(path);
  assert.throws(() => acquireTargetLock(path), /already|progress/);
  release();
  const stale = join(path, "maintenance.lock");
  writeFileSync(stale, JSON.stringify({ pid: 2147483647, token: "stale" }));
  const releaseRecovered = acquireTargetLock(path);
  releaseRecovered();
});

test("deployment records are atomically replaced rather than appended or partially published", t => {
  const path = join(directory(t), "deployment.json");
  atomicJson(path, { revision: "old" }); atomicJson(path, { revision: "verified" });
  assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), { revision: "verified" });
});

function actions({ pending = [29], failAt, build = true } = {}) {
  const calls = [];
  let migrated = false;
  const handlers = Object.fromEntries(["ensureImages", "ensureDatabase", "stopApplications", "backup", "migrate", "startServices", "verifyReady", "commitDeployment", "recordFailure"].map(name => [name, async () => {
    if (name !== "ensureImages" || build) calls.push(name);
    if (name === failAt) throw new Error(`failed ${name}`);
    if (name === "migrate") migrated = true;
  }]));
  handlers.checkSchema = async () => { calls.push("checkSchema"); return { expected_version: 29, applied_versions: migrated ? Array.from({ length: 29 }, (_, i) => i + 1) : [28], pending_versions: migrated ? [] : pending }; };
  return { calls, handlers };
}

test("start after merge verifies backup before migrations and publishes success only after readiness", async () => {
  const { calls, handlers } = actions();
  await executeLifecycle({ recreate: true }, handlers);
  assert.deepEqual(calls, ["ensureImages", "stopApplications", "ensureDatabase", "checkSchema", "backup", "migrate", "checkSchema", "startServices", "verifyReady", "commitDeployment"]);
});

test("changed dependency preparation stops writers before dependency recreation even on the same revision", async () => {
  const { calls, handlers } = actions({ pending: [] });
  handlers.ensureImages = async () => { calls.push("ensureImages"); return { dependenciesMayChange: true }; };
  handlers.ensureDatabase = async options => { calls.push("ensureDatabase"); assert.equal(options.allowRecreation, true); };
  await executeLifecycle({ recreate: false }, handlers);
  assert.deepEqual(calls.slice(0, 3), ["ensureImages", "stopApplications", "ensureDatabase"]);
  assert(!calls.includes("backup") && !calls.includes("migrate"));
});

test("dependency recreation failure leaves writers stopped and never publishes a deployment", async () => {
  const { calls, handlers } = actions({ failAt: "ensureDatabase", pending: [] });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), /failed ensureDatabase/);
  assert.deepEqual(calls, ["ensureImages", "stopApplications", "ensureDatabase", "stopApplications", "recordFailure"]);
});

test("repeated compatible start avoids backup migration and application shutdown", async () => {
  const { calls, handlers } = actions({ pending: [], build: false });
  handlers.ensureDatabase = async options => { calls.push("ensureDatabase"); assert.equal(options.allowRecreation, false); };
  await executeLifecycle({ recreate: false }, handlers);
  assert.deepEqual(calls, ["ensureDatabase", "checkSchema", "startServices", "verifyReady", "commitDeployment"]);
});

test("failed image preparation leaves the existing application running", async () => {
  const { calls, handlers } = actions({ failAt: "ensureImages" });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), /failed ensureImages/);
  assert(!calls.includes("stopApplications")); assert(!calls.includes("startServices"));
});

for (const failure of ["backup", "migrate"]) test(`failed ${failure} prevents dependent rollout and preserves failure evidence`, async () => {
  const { calls, handlers } = actions({ failAt: failure });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), new RegExp(`failed ${failure}`));
  assert(!calls.includes("startServices")); assert(!calls.includes("commitDeployment"));
  assert.equal(calls.at(-1), "recordFailure");
});

test("failed upgraded readiness stops application writers without restoring an older database", async () => {
  const { calls, handlers } = actions({ failAt: "verifyReady" });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), /failed verifyReady/);
  assert.equal(calls.filter(name => name === "stopApplications").length, 2);
  assert(!calls.includes("commitDeployment"));
});

function commandFixture(t, mode) {
  const fixture = cliFixture(mode);
  t.after(() => rmSync(fixture.directory, { recursive: true, force: true }));
  return fixture;
}

test("actual CLI upgrades with a verified backup and keeps repeat starts free of build migration or stop", t => {
  const fixture = commandFixture(t, "upgrade");
  const first = fixture.command("start", "--no-open");
  assert.equal(first.status, 0, first.stdout + first.stderr);
  assert(first.stdout.includes("Tempo ready"));
  const calls = fixture.calls();
  const backup = calls.findIndex(call => call.args.some(argument => argument.includes("pg_dump")));
  const migration = calls.findIndex(call => call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check"));
  assert(backup >= 0 && migration > backup);
  const beforeRepeat = calls.length;
  const repeated = fixture.command("start", "--no-open");
  assert.equal(repeated.status, 0, repeated.stdout + repeated.stderr);
  for (const call of fixture.calls().slice(beforeRepeat)) assert(!call.args.includes("build") && !call.args.includes("stop")
    && !(call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check")));
  for (const call of fixture.calls().slice(beforeRepeat).filter(call => call.args.includes("up") && call.args.includes("postgres")))
    assert(call.args.includes("--no-recreate"), "compatible dependency checks cannot recreate running infrastructure");
  const record = JSON.parse(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"));
  assert.equal(record.schema, 29); assert.equal(record.backup.verified, true);
});

test("actual CLI dependency startup failure occurs after writer shutdown and keeps writers stopped", t => {
  const fixture = commandFixture(t, "dependency-fail");
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  const calls = fixture.calls();
  const preparation = calls.findIndex(call => call.args.includes("build"));
  const stop = calls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
  const dependencies = calls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
  assert(preparation >= 0 && stop > preparation && dependencies > stop);
  const remaining = JSON.parse(readFileSync(join(fixture.directory, "machine.json"), "utf8")).running;
  assert(!remaining.some(name => ["api", "foreground-worker", "background-worker", "background-scheduler"].includes(name)));
  assert(!calls.slice(dependencies + 1).some(call => call.args.includes("up") && call.args.includes("api")));
});

test("actual CLI falls back explicitly after failed CI and preserves local edits without fetching", t => {
  for (const mode of ["ci-fail", "dirty"]) {
    const fixture = commandFixture(t, mode);
    const result = fixture.command("start", "--no-open");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert(result.stdout.includes("update remains blocked"));
    assert(!fixture.calls().some(call => call.args.includes("build") || call.args.includes("merge")));
    if (mode === "dirty") assert(!fixture.calls().some(call => call.args.includes("fetch")));
  }
});

test("actual CLI read-only maintenance plan changes no source images services or database", t => {
  const fixture = commandFixture(t, "upgrade");
  const result = fixture.command("migrate", "--plan");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  for (const call of fixture.calls()) assert(!["fetch", "merge", "build", "run", "up", "stop", "pull"].some(argument => call.args.includes(argument)));
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
});

test("actual CLI failed migration never starts dependents and requires explicit inspected retry", t => {
  const fixture = commandFixture(t, "migration-fail");
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  const calls = fixture.calls();
  const migration = calls.findIndex(call => call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check"));
  assert(migration >= 0);
  assert(!calls.slice(migration + 1).some(call => call.args.includes("up") && call.args.includes("api")));
  const beforeRepeat = calls.length;
  const repeat = fixture.command("migrate");
  assert.notEqual(repeat.status, 0); assert(repeat.stderr.includes("--retry"));
  assert(!fixture.calls().slice(beforeRepeat).some(call => call.args.includes("scripts/apply_postgres_migrations.py")));
});

test("actual CLI failed builds preserve running services and failure diagnostics redact passwords", t => {
  const fixture = commandFixture(t, "build-fail");
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  assert(!fixture.calls().some(call => call.args.includes("stop")));
  const backupFixture = commandFixture(t, "backup-fail");
  const machine = JSON.parse(readFileSync(join(backupFixture.directory, "machine.json"), "utf8"));
  machine.schema = 28; writeFileSync(join(backupFixture.directory, "machine.json"), JSON.stringify(machine));
  const failedBackup = backupFixture.command("start", "--no-open");
  assert.notEqual(failedBackup.status, 0);
  assert(!(failedBackup.stdout + failedBackup.stderr).includes("canary-private-password"));
  assert(!readFileSync(join(backupFixture.stateDirectory, "operation.json"), "utf8").includes("canary-private-password"));
});

test("actual CLI backup restores the prior running service state and retains the failed backup phase", t => {
  const fixture = commandFixture(t, "backup-fail");
  const before = JSON.parse(readFileSync(join(fixture.directory, "machine.json"), "utf8"));
  const result = fixture.command("backup");
  assert.notEqual(result.status, 0);
  const after = JSON.parse(readFileSync(join(fixture.directory, "machine.json"), "utf8"));
  assert.deepEqual(after.running.sort(), before.running.sort());
  const operation = JSON.parse(readFileSync(join(fixture.stateDirectory, "operation.json"), "utf8"));
  assert.equal(operation.phase, "failed"); assert.equal(operation.failed_phase, "verifying_backup");
  assert(!(result.stdout + result.stderr + JSON.stringify(operation)).includes("canary-private-password"));
});

test("actual CLI blocked update rejects an incomplete recorded image set before changing services", t => {
  const fixture = commandFixture(t, "ci-fail");
  const recordPath = join(fixture.stateDirectory, "deployment.json");
  const record = JSON.parse(readFileSync(recordPath, "utf8"));
  delete record.images.api; writeFileSync(recordPath, JSON.stringify(record));
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  assert(result.stderr.includes("image receipt"));
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop")));
});

test("actual CLI backup restores stopped state when database validation fails before the backup", t => {
  const fixture = commandFixture(t, "status-fail");
  const machinePath = join(fixture.directory, "machine.json");
  const machine = JSON.parse(readFileSync(machinePath, "utf8"));
  machine.running = []; writeFileSync(machinePath, JSON.stringify(machine));
  const result = fixture.command("backup");
  assert.notEqual(result.status, 0); assert(result.stderr.includes("roles"));
  assert.deepEqual(JSON.parse(readFileSync(machinePath, "utf8")).running, []);
  assert(!fixture.calls().some(call => call.args.some(argument => argument.includes("pg_dump"))));
});

test("actual CLI detects checkout edits during image preparation before touching the running application", t => {
  const fixture = commandFixture(t, "edited-during-build");
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0); assert(result.stderr.includes("Checkout changed"));
  assert(fixture.calls().some(call => call.args.includes("build")));
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop")));
});

for (const [name, alter] of [
  ["Redis cannot additionally mount registered PostgreSQL data", config => config.services.redis.volumes.push({ type: "volume", source: "tempo-postgres-data", target: "/data" })],
  ["Redis cannot move its registered data to another destination", config => { config.services.redis.volumes[0].target = "/wrong-data"; }],
  ["PostgreSQL cannot substitute registered Redis data", config => { config.services.postgres.volumes[0].source = "tempo-redis-data"; }],
  ["defense engine cannot mount registered PostgreSQL data", config => config.services["defense-engine"].volumes.push({ type: "volume", source: "tempo-postgres-data", target: "/state" })],
  ["required backup persistent mount cannot be removed", config => { config.services["postgres-backup"].volumes = []; }],
]) test(`CLI volume ownership: ${name}`, t => {
  const { config, target } = fixture(t);
  alter(config);
  assert.throws(() => validateTarget(config, target), /mount|volume/i);
});
