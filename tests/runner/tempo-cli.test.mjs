import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync as fsSpawnSync } from "node:child_process";
const cliEntry = new URL("../../scripts/tempo-cli.mjs", import.meta.url).pathname;
import fs, { existsSync, mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { schemaVersionFromSource, validateTarget, validateContainers, deploymentCanStart,
  acquireTargetLock, assessMainVerification, assessCandidate, qualityEvidence, atomicJson, selectCandidate, productVolumes, targetKey,
  CommandExecutionError, commandExecutor, inspectCandidateSource, isCandidateBlocker, isOperationalGitFailure, sourceFingerprint, sourceMatchesFingerprint } from "../../scripts/tempo-deployment.mjs";
import { assessMigrationRecovery, configurationFingerprint, createRuntime, executeLifecycle } from "../../scripts/tempo-runtime.mjs";
import { cliFixture } from "./tempo-cli-fixture.mjs";

test("actual CLI diagnostics explain current blocker before historical Redis failure", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "failed",
    revision: "b".repeat(40), started_at: "2026-10-01T00:00:00Z", failed_phase: "checking_database",
    failure: "Redis is not ready: expected PONG." }));
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Run: tempo start/);
  assert.match(result.stdout, /wait up to 30 minutes/);
  assert.match(result.stdout, /previous Redis error is historical; Redis responds now/i);
  assert(!result.stdout.includes("Applied schema versions:"));
  assert(!result.stdout.includes("Last operation: failed"));
  assert.equal(result.stdout.match(/Run:/g)?.length, 1);
});

test("actual CLI diagnostics keep technical evidence in verbose output with legacy timestamps identified", t => {
  const fixture = diagnosticFixture(t);
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "failed", failure: "old failure" }));
  const result = fixture.command("doctor", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Applied schema versions:/);
  assert.match(result.stdout, /Historical operation:/);
  assert.match(result.stdout, /timestamp not recorded/);
  assert.match(result.stdout, /revision not recorded/);
});

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
  const config = { name: "tempo", volumes: target.volumes, secrets: { password: { file: secret }, writer_pgpass: { file: secret } },
    services: { postgres: { image: "postgres:18.6-trixie", environment: { POSTGRES_DB: "tempo" },
      volumes: [{ type: "volume", source: "tempo-postgres-data", target: "/var/lib/postgresql" }] }, web: { ports: [{ host_ip: "127.0.0.1", published: "3000", target: 80 }] },
      redis: { volumes: [{ type: "volume", source: "tempo-redis-data", target: "/data" }] },
      "postgres-backup": { volumes: [{ type: "volume", source: "tempo-postgres-backups", target: "/backups" }], tmpfs: ["/var/lib/postgresql"] },
      "defense-engine": { volumes: [{ type: "volume", source: "tempo-engine-operations", target: "/state" }] },
      api: { environment: { TEMPO_DATABASE_READ_URL: "postgresql://tempo_reader@postgres:5432/tempo" } } } };
  for (const name of ["foreground-worker", "background-worker"]) config.services[name] = {
    environment: { TEMPO_DATABASE_WRITE_URL: "postgresql://tempo_writer@postgres:5432/tempo",
      TEMPO_DATABASE_READ_URL: "postgresql://tempo_writer@postgres:5432/tempo",
      PGPASSFILE: "/run/secrets/writer_pgpass", TEMPO_REDIS_URL: "redis://redis:6379/0" },
    secrets: [{ source: "writer_pgpass", target: "writer_pgpass", mode: 0o400 }],
  };
  config.services["background-scheduler"] = { environment: { TEMPO_REDIS_URL: "redis://redis:6379/0" } };
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

for (const [name, alter] of [
  ["foreground missing writer URL", config => { delete config.services["foreground-worker"].environment.TEMPO_DATABASE_WRITE_URL; }],
  ["foreground reader role as writer", config => { config.services["foreground-worker"].environment.TEMPO_DATABASE_WRITE_URL = "postgresql://tempo_reader@postgres:5432/tempo"; }],
  ["foreground writer to another database", config => { config.services["foreground-worker"].environment.TEMPO_DATABASE_WRITE_URL = "postgresql://tempo_writer@postgres:5432/other"; }],
  ["foreground writer to another host", config => { config.services["foreground-worker"].environment.TEMPO_DATABASE_WRITE_URL = "postgresql://tempo_writer@elsewhere:5432/tempo"; }],
  ["background missing read URL", config => { delete config.services["background-worker"].environment.TEMPO_DATABASE_READ_URL; }],
  ["background reader role as reader", config => { config.services["background-worker"].environment.TEMPO_DATABASE_READ_URL = "postgresql://tempo_reader@postgres:5432/tempo"; }],
  ["foreground SQLite fallback", config => { config.services["foreground-worker"].environment.TEMPO_DB_PATH = "/state/tempo.db"; }],
  ["background empty SQLite fallback variable", config => { config.services["background-worker"].environment.TEMPO_DB_PATH = ""; }],
  ["foreground PGPASSWORD", config => { config.services["foreground-worker"].environment.PGPASSWORD = "wrong-password"; }],
  ["background PGPASSWORD", config => { config.services["background-worker"].environment.PGPASSWORD = "wrong-password"; }],
  ["empty PGPASSWORD", config => { config.services["foreground-worker"].environment.PGPASSWORD = ""; }],
  ["foreground PGHOSTADDR redirect", config => { config.services["foreground-worker"].environment.PGHOSTADDR = "192.0.2.99"; }],
  ["background PGSERVICE credentials", config => { config.services["background-worker"].environment.PGSERVICE = "competing"; }],
  ["foreground missing passfile", config => { delete config.services["foreground-worker"].environment.PGPASSFILE; }],
  ["background wrong passfile", config => { config.services["background-worker"].environment.PGPASSFILE = "/run/secrets/reader_pgpass"; }],
  ["foreground writer secret unattached", config => { config.services["foreground-worker"].secrets = []; }],
  ["background wrong secret at writer passfile", config => { config.services["background-worker"].secrets[0].source = "password"; }],
  ["foreground writer secret at wrong target", config => { config.services["foreground-worker"].secrets[0].target = "other_pgpass"; }],
  ["background duplicate passfile target", config => { config.services["background-worker"].secrets.push({ source: "password", target: "/run/secrets/writer_pgpass" }); }],
  ["writer secret undefined globally", config => { delete config.secrets.writer_pgpass; }],
  ["foreground public passfile mode", config => { config.services["foreground-worker"].secrets[0].mode = 0o444; }],
  ["foreground wrong broker", config => { config.services["foreground-worker"].environment.TEMPO_REDIS_URL = "redis://other:6379/0"; }],
  ["background wrong broker database", config => { config.services["background-worker"].environment.TEMPO_REDIS_URL = "redis://redis:6379/1"; }],
  ["scheduler missing broker", config => { delete config.services["background-scheduler"].environment.TEMPO_REDIS_URL; }],
  ["missing foreground service", config => { delete config.services["foreground-worker"]; }],
  ["missing background service", config => { delete config.services["background-worker"]; }],
]) test(`CLI worker storage contract rejects ${name}`, t => {
  const { config, target } = fixture(t);
  alter(config);
  assert.throws(() => validateTarget(config, target), /worker|scheduler/i);
});

test("CLI worker storage contract accepts production identities and resolved secret targets with private or omitted mode", t => {
  const { config, target } = fixture(t);
  assert.doesNotThrow(() => validateTarget(config, target));
  config.services["background-worker"].secrets[0].mode = "0400";
  config.services["background-worker"].secrets[0].target = "/run/secrets/writer_pgpass";
  delete config.services["foreground-worker"].secrets[0].mode; // Local Compose omits unsupported secret modes; file permissions are checked separately.
  assert.doesNotThrow(() => validateTarget(config, target));
});

test("CLI worker storage contract permits libpq defaults already pinned by the explicit DSN", t => {
  const { config, target } = fixture(t);
  for (const name of ["foreground-worker", "background-worker"]) Object.assign(config.services[name].environment, {
    PGHOST: "unused-host", PGPORT: "9999", PGDATABASE: "unused-database", PGUSER: "unused-role",
    PGSERVICEFILE: "/unused/service-file", // No service is selected; these defaults cannot replace the explicit DSN/passfile.
  });
  assert.doesNotThrow(() => validateTarget(config, target));
});

test("CLI worker storage contract rejects PGPASSWORD before mutations and keeps credentials out of output and saved state", t => {
  const fixture = commandFixture(t, "upgrade");
  const data = readFixtureJson(fixture, "fixture.json");
  const password = "canary-worker-password-do-not-publish";
  data.config.services["foreground-worker"].environment.PGPASSWORD = password;
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(data.config));
  const deploymentPath = join(fixture.stateDirectory, "deployment.json");
  const previousDeployment = readFileSync(deploymentPath, "utf8");
  const journalPath = join(fixture.stateDirectory, "operation.json");
  const failurePath = join(fixture.stateDirectory, "failure-preserved.log");
  writeFileSync(journalPath, JSON.stringify({ phase: "ready", revision: "b".repeat(40) }));
  writeFileSync(failurePath, "Previously sanitized diagnostic\n");
  const previousJournal = readFileSync(journalPath, "utf8"), previousFailure = readFileSync(failurePath, "utf8");
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  assert(result.stderr.includes("foreground-worker") && result.stderr.includes("PGPASSWORD"));
  assert(result.stderr.includes("/run/secrets/writer_pgpass"));
  assert(!result.stdout.includes(password) && !result.stderr.includes(password));
  assert(!fixture.calls().some(call => ["build", "pull", "up", "stop", "merge"].some(command => call.args.includes(command))
    || call.args.includes("scripts/apply_postgres_migrations.py")));
  assert.equal(readFileSync(deploymentPath, "utf8"), previousDeployment);
  assert.equal(readFileSync(journalPath, "utf8"), previousJournal);
  assert.equal(readFileSync(failurePath, "utf8"), previousFailure);
  for (const name of readdirSync(fixture.stateDirectory)) {
    assert(fs.statSync(join(fixture.stateDirectory, name)).isFile(), "no candidate release directory may be written");
    assert(!readFileSync(join(fixture.stateDirectory, name), "utf8").includes(password));
  }
  assert.deepEqual(readdirSync(fixture.stateDirectory).sort(), ["deployment.json", "failure-preserved.log", "operation.json"]);
});

test("CLI worker storage contract blocks actual maintenance before any deployment command", t => {
  const fixture = commandFixture(t, "upgrade");
  const data = readFixtureJson(fixture, "fixture.json");
  delete data.config.services["foreground-worker"].environment.TEMPO_DATABASE_WRITE_URL;
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(data.config));
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  assert(result.stderr.includes("foreground-worker") && result.stderr.includes("TEMPO_DATABASE_WRITE_URL"));
  assert(!fixture.calls().some(call => ["build", "pull", "up", "stop", "merge"].some(command => call.args.includes(command))));
  assert.equal(readFixtureJson(fixture, "deployment.json", true).revision, "b".repeat(40));
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
  const jobs = ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify", "lifecycle / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, conclusion: "success", status: "completed" }));
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

function observeAtomicFileSystem(t, failOperation) {
  const events = [];
  const pathsByDescriptor = new Map();
  for (const method of ["openSync", "writeFileSync", "fsyncSync", "closeSync", "renameSync"]) {
    const original = fs[method];
    t.mock.method(fs, method, (...args) => {
      const path = method === "openSync" || method === "renameSync" ? args[0] : pathsByDescriptor.get(args[0]);
      const event = { method, path, directory: method === "fsyncSync" && fs.fstatSync(args[0]).isDirectory(), destination: method === "renameSync" ? args[1] : undefined };
      events.push(event);
      if (failOperation?.(event, events)) throw new Error("injected filesystem failure");
      const result = original(...args);
      if (method === "openSync") pathsByDescriptor.set(result, path);
      if (method === "closeSync") pathsByDescriptor.delete(args[0]);
      return result;
    });
  }
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  return events;
}

test("deployment records fsync file contents before rename and the containing directory afterward", t => {
  const path = join(directory(t), "deployment.json");
  const events = observeAtomicFileSystem(t);
  atomicJson(path, { revision: "verified" });
  assert.deepEqual(events.map(event => event.method), ["openSync", "writeFileSync", "fsyncSync", "closeSync", "renameSync", "openSync", "fsyncSync", "closeSync"]);
  assert.equal(events[2].directory, false);
  assert.equal(events[6].directory, true);
  assert.equal(events[5].path, join(path, ".."));
  assert.equal(events[4].destination, path);
});

test("deployment records surface directory fsync failure and retain the renamed destination", t => {
  const stateDirectory = directory(t), path = join(stateDirectory, "migration-guard.json");
  atomicJson(path, { state: "old" });
  const events = observeAtomicFileSystem(t, event => event.directory);
  assert.throws(() => atomicJson(path, { state: "pending" }), /deployment state.*durab/i);
  assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), { state: "pending" });
  assert.deepEqual(readdirSync(stateDirectory), ["migration-guard.json"]);
  assert.equal(events.at(-1).method, "closeSync", "directory descriptor closes after fsync failure");
});

for (const method of ["writeFileSync", "fsyncSync", "renameSync"]) test(`deployment records clean temporary files after ${method} failure without replacing existing state`, t => {
  const stateDirectory = directory(t), path = join(stateDirectory, "deployment.json");
  atomicJson(path, { revision: "old" });
  observeAtomicFileSystem(t, event => event.method === method);
  assert.throws(() => atomicJson(path, { revision: "new" }), /injected filesystem failure/);
  assert.deepEqual(JSON.parse(readFileSync(path, "utf8")), { revision: "old" });
  assert.deepEqual(readdirSync(stateDirectory), ["deployment.json"]);
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

test("failed application identity inspection stops unconfirmed writers before database validation", async () => {
  const { calls, handlers } = actions();
  handlers.recordedApplicationsMatch = async () => { throw new Error("container inspection unavailable"); };
  await assert.rejects(executeLifecycle({ recreate: false }, handlers), /container inspection unavailable/);
  assert(calls.includes("stopApplications"));
  assert(!calls.includes("ensureDatabase") && !calls.includes("checkSchema") && !calls.includes("startServices") && !calls.includes("commitDeployment"));
  assert(calls.includes("recordFailure"));
});

function commandFixture(t, mode) {
  const fixture = cliFixture(mode);
  t.after(() => rmSync(fixture.directory, { recursive: true, force: true }));
  return fixture;
}

function readFixtureJson(fixture, name, state = false) {
  return JSON.parse(readFileSync(join(state ? fixture.stateDirectory : fixture.directory, name), "utf8"));
}

function editFixtureJson(fixture, name, alter, state = false) {
  const value = readFixtureJson(fixture, name, state); alter(value);
  writeFileSync(join(state ? fixture.stateDirectory : fixture.directory, name), JSON.stringify(value));
}

function assertOriginalHistory(fixture) {
  const guard = readFixtureJson(fixture, "migration-guard.json", true);
  assert.equal(guard.study_invariants.reviews.digest, "preserved");
  assert.equal(guard.backup.verified, true);
  assert.equal(fixture.calls().filter(call => call.args.includes("scripts/verify_postgres_cli_state.py") && !call.args.includes("--expected")).length, 1,
    "an unresolved attempt never captures a new baseline");
  assert.equal(fixture.calls().filter(call => call.args.some(argument => argument.includes("pg_dump"))).length, 1,
    "retries retain the restore-verified original backup");
  return guard;
}

test("actual CLI migration guard partial commits retain H0 through failed and repaired retries", t => {
  const fixture = commandFixture(t, "partial-fail");
  assert.notEqual(fixture.command("migrate").status, 0);
  assert.equal(readFixtureJson(fixture, "machine.json").schema, 27);
  editFixtureJson(fixture, "fixture.json", value => { value.mode = "repair"; });
  const retry = fixture.command("migrate", "--retry");
  assert.notEqual(retry.status, 0, "retry must detect the original mutation after remaining migrations commit");
  const original = assertOriginalHistory(fixture);
  assert.deepEqual(readFixtureJson(fixture, "machine.json").migrationVersions, [27, 28, 29]);
  assert.deepEqual(assertOriginalHistory(fixture).study_invariants, original.study_invariants);
  assert.equal(readFixtureJson(fixture, "migration-guard.json", true).state, "pending");
  editFixtureJson(fixture, "machine.json", value => { value.history = "preserved"; });
  const repaired = fixture.command("migrate", "--retry");
  assert.equal(repaired.status, 0, repaired.stdout + repaired.stderr);
  assert.equal(assertOriginalHistory(fixture).state, "verified");
});

test("actual CLI migration guard completed schema still verifies H0 before repaired startup", t => {
  const fixture = commandFixture(t, "history-fail");
  const deploymentBefore = readFixtureJson(fixture, "deployment.json", true);
  assert.notEqual(fixture.command("migrate").status, 0);
  assert.equal(readFixtureJson(fixture, "machine.json").schema, 29);
  const retry = fixture.command("migrate", "--retry");
  assert.notEqual(retry.status, 0, "a current ledger must not bypass the failed history check");
  assert.match(retry.stderr, /history/);
  const guard = assertOriginalHistory(fixture);
  assert(retry.stderr.includes(guard.backup.filename), "diagnostics retain the original backup reference");
  assert.deepEqual(readFixtureJson(fixture, "deployment.json", true), deploymentBefore);
  assert(!readFixtureJson(fixture, "machine.json").running.includes("api"));
  assert.equal(readFixtureJson(fixture, "machine.json").migrations, 1);
  editFixtureJson(fixture, "machine.json", value => { value.history = "preserved"; });
  const repaired = fixture.command("start", "--retry", "--no-open");
  assert.equal(repaired.status, 0, repaired.stdout + repaired.stderr);
  assert.equal(assertOriginalHistory(fixture).state, "verified");
  assert.equal(readFixtureJson(fixture, "operation.json", true).phase, "ready");
  assert.notDeepEqual(readFixtureJson(fixture, "deployment.json", true), deploymentBefore);
});

test("actual CLI migration guard newer candidates cannot bypass H0 or retry authorization", t => {
  const fixture = commandFixture(t, "history-fail");
  assert.notEqual(fixture.command("migrate").status, 0);
  editFixtureJson(fixture, "fixture.json", value => { value.mode = "repair"; value.revision = "c".repeat(40); });
  editFixtureJson(fixture, "machine.json", value => { value.head = "c".repeat(40); });
  const ordinary = fixture.command("start", "--no-open");
  assert.notEqual(ordinary.status, 0, "changing the selected revision cannot authorize continuation");
  assert.match(ordinary.stderr, /--retry/);
  const retried = fixture.command("migrate", "--retry");
  assert.notEqual(retried.status, 0);
  const guard = assertOriginalHistory(fixture);
  assert.equal(guard.origin_revision, "a".repeat(40));
  assert.equal(guard.last_attempted_revision, "c".repeat(40));
  assert(!readFixtureJson(fixture, "machine.json").running.includes("api"));
});

test("actual CLI migration guard interrupted attempts survive loss of the operation journal", t => {
  const fixture = commandFixture(t, "interrupted");
  const interrupted = fixture.command("migrate");
  assert.equal(interrupted.signal, "SIGKILL", interrupted.stdout + interrupted.stderr);
  assert.equal(readFixtureJson(fixture, "machine.json").migrations, 0);
  assert.equal(readFixtureJson(fixture, "operation.json", true).phase, "applying_migrations");
  const guard = assertOriginalHistory(fixture);
  rmSync(join(fixture.stateDirectory, "operation.json"));
  const ordinary = fixture.command("migrate");
  assert.notEqual(ordinary.status, 0);
  assert.match(ordinary.stderr, /--retry/);
  assert.deepEqual(readFixtureJson(fixture, "migration-guard.json", true), guard);
  editFixtureJson(fixture, "fixture.json", value => { value.mode = "repair"; });
  editFixtureJson(fixture, "machine.json", value => { value.history = "preserved"; });
  const retry = fixture.command("migrate", "--retry");
  assert.equal(retry.status, 0, retry.stdout + retry.stderr);
  assert.equal(assertOriginalHistory(fixture).state, "verified");
});

function imageRuntimeFixture(t, reference, actualMajor, fallback = false, responseForCommand = () => undefined, runtimeOptions = {}) {
  const fixture = commandFixture(t, "upgrade");
  const config = readFixtureJson(fixture, "fixture.json").config;
  config.services.postgres.image = reference;
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(config));
  const record = readFixtureJson(fixture, "deployment.json", true);
  const calls = [];
  const run = async (_command, args, options) => {
    calls.push(args);
    const response = responseForCommand(args, options);
    if (response !== undefined) return typeof response === "string" ? { stdout: response, stderr: "", code: 0 } : response;
    let result = "";
    if (args.includes("config")) {
      const resolved = structuredClone(config);
      for (let index = 0; index < args.length; index++) if (args[index] === "-f") {
        const overlay = JSON.parse(readFileSync(args[index + 1], "utf8"));
        for (const [name, service] of Object.entries(overlay.services)) resolved.services[name] = { ...resolved.services[name], ...service };
      }
      result = args.includes("--hash") ? "postgres hash\nredis hash" : JSON.stringify(resolved);
    } else if (args.includes("image") && args.includes("inspect")) result = JSON.stringify([{ Id: args.at(-1), Config: { Labels: { "org.opencontainers.image.revision": record.revision } } }]);
    else if (args.includes("--version")) result = `postgres (PostgreSQL) ${actualMajor}.6 (test)`;
    return { stdout: result, stderr: "", code: 0 };
  };
  const runtime = createRuntime(fixture.target, { run, stateDirectory: fixture.stateDirectory, revision: record.revision,
    evidence: record.evidence, previous: record, fallback,
    preparedImages: { revision: record.revision, images: record.images, configFingerprint: configurationFingerprint(config) }, log: () => {}, ...runtimeOptions });
  return { runtime, calls, fixture };
}

function redisRuntimeFixture(t, replies, readinessMilliseconds = 180_000) {
  let elapsedMilliseconds = 0;
  let answeredPong = false;
  const probes = [];
  const waits = [];
  const result = imageRuntimeFixture(t, "postgres:18.6-trixie", 18, false, (args, options) => {
    if (args.includes("--mount")) return "18";
    if (args.includes("ping")) {
      probes.push({ elapsedMilliseconds, options });
      const reply = replies[Math.min(probes.length - 1, replies.length - 1)];
      if (reply instanceof Error) { elapsedMilliseconds += 5000; throw reply; }
      answeredPong = reply.code === 0 && reply.stdout.trim() === "PONG";
      return reply;
    }
  }, { redisReadinessOptions: { timeoutMilliseconds: readinessMilliseconds, now: () => elapsedMilliseconds,
    wait: async milliseconds => { waits.push(milliseconds); elapsedMilliseconds += milliseconds; } } });
  return { ...result, probes, waits, answeredPong: () => answeredPong, elapsedMilliseconds: () => elapsedMilliseconds };
}

test("CLI Redis readiness waits for saved legacy healthcheck loading before schema migration or application startup", async t => {
  const fixture = redisRuntimeFixture(t, [{ code: 0, stdout: "LOADING Redis is loading the dataset in memory\n", stderr: "" },
    { code: 0, stdout: "PONG\n", stderr: "" }]);
  await fixture.runtime.config();
  const { calls, handlers } = actions();
  handlers.ensureImages = fixture.runtime.ensureImages;
  handlers.ensureDatabase = fixture.runtime.ensureDatabase;
  for (const name of ["checkSchema", "backup", "migrate", "startServices", "commitDeployment"]) {
    const original = handlers[name];
    handlers[name] = async (...args) => { assert(fixture.answeredPong(), `${name} must wait for actual PONG`); return original(...args); };
  }
  await executeLifecycle({ recreate: true }, handlers);
  assert.equal(fixture.probes.length, 2);
  assert.deepEqual(fixture.waits, [1000]);
  assert(calls.includes("migrate") && calls.includes("startServices") && calls.includes("commitDeployment"));
  assert(fixture.probes.every(probe => probe.options.allowFailure && probe.options.timeout <= 5000));
});

test("CLI Redis readiness retries strict loading error replies and connection refusal until PONG", async t => {
  const fixture = redisRuntimeFixture(t, [
    { code: 1, stdout: "", stderr: "LOADING Redis is loading the dataset in memory\n" },
    { code: 1, stdout: "", stderr: "Could not connect to Redis at 127.0.0.1:6379: Connection refused\n" },
    { code: 0, stdout: "PONG\n", stderr: "" },
  ]);
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  await fixture.runtime.ensureDatabase();
  assert.equal(fixture.probes.length, 3);
  assert.deepEqual(fixture.waits, [1000, 1000]);
  assert(fixture.answeredPong());
});

test("CLI Redis readiness retries server EOF until PONG with bounded probes", async t => {
  const fixture = redisRuntimeFixture(t, [
    { code: 1, stdout: "", stderr: "Error: Server closed the connection" },
    { code: 0, stdout: "PONG", stderr: "" },
  ]);
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  await fixture.runtime.ensureDatabase();
  assert(fixture.answeredPong());
  assert.equal(fixture.probes.length, 2);
  assert.deepEqual(fixture.waits, [1000]);
  assert.deepEqual(fixture.probes.map(probe => probe.options.timeout), [5000, 5000]);
  assert(fixture.probes.every(probe => probe.options.allowFailure));
});

test("CLI Redis readiness probes terminal errors before Docker health retries and preserves PostgreSQL readiness", async t => {
  const blocked = redisRuntimeFixture(t, [{ code: 1, stdout: "", stderr: "NOAUTH Authentication required" }]);
  await blocked.runtime.config(); await blocked.runtime.ensureImages();
  await assert.rejects(blocked.runtime.ensureDatabase(), /NOAUTH Authentication required/);
  const redisStartup = blocked.calls.find(args => args.includes("up") && args.includes("redis"));
  assert(redisStartup && !redisStartup.includes("--wait"), "Docker health retries must not hide terminal replies or shorten the CLI deadline");
  assert(!blocked.calls.some(args => args.includes("up") && args.includes("--wait")), "terminal Redis failures prevent further readiness work");

  const ready = redisRuntimeFixture(t, [{ code: 0, stdout: "PONG", stderr: "" }]);
  await ready.runtime.config(); await ready.runtime.ensureImages();
  await ready.runtime.ensureDatabase();
  const postgresWaitIndex = ready.calls.findIndex(args => args.includes("up") && args.includes("--wait") && args.includes("postgres"));
  assert(postgresWaitIndex > ready.calls.findIndex(args => args.includes("ping")));
  assert(!ready.calls[postgresWaitIndex].includes("redis"));
  assert(ready.calls[postgresWaitIndex].includes("--no-recreate"));
});

test("CLI Redis readiness retries interrupted and timed-out probes with bounded remaining deadlines", async t => {
  const fixture = redisRuntimeFixture(t, [new Error("Redis probe timed out"),
    { code: null, stdout: "", stderr: "" }, { code: 0, stdout: "PONG", stderr: "" }], 8000);
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  await fixture.runtime.ensureDatabase();
  assert.equal(fixture.probes.length, 3);
  assert.deepEqual(fixture.probes.map(probe => probe.options.timeout), [5000, 2000, 1000]);
  assert(fixture.answeredPong());
});

test("CLI Redis readiness expires at 180 seconds and records the real loading reply without migration startup or publication", async t => {
  const fixture = redisRuntimeFixture(t, [{ code: 1, stdout: "LOADING Redis is loading the dataset in memory", stderr: "" }]);
  const receiptPath = join(fixture.fixture.stateDirectory, "deployment.json");
  const receiptBefore = readFileSync(receiptPath, "utf8");
  await fixture.runtime.config();
  const { calls, handlers } = actions();
  Object.assign(handlers, { ensureImages: fixture.runtime.ensureImages, ensureDatabase: fixture.runtime.ensureDatabase,
    recordFailure: fixture.runtime.recordFailure });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), /180\.0 seconds \(checking_redis\): LOADING Redis is loading/);
  assert.equal(fixture.elapsedMilliseconds(), 180_000);
  assert.equal(fixture.probes.length, 180);
  assert(!calls.some(name => ["checkSchema", "backup", "migrate", "startServices", "commitDeployment"].includes(name)));
  assert.equal(readFileSync(receiptPath, "utf8"), receiptBefore);
  const operation = JSON.parse(readFileSync(join(fixture.fixture.stateDirectory, "operation.json"), "utf8"));
  assert.equal(operation.phase, "failed");
  assert.equal(operation.failed_phase, "checking_redis");
  assert(!Number.isNaN(Date.parse(operation.failed_at)));
  assert.match(operation.failure, /LOADING Redis is loading/);
});

test("CLI Redis readiness expires at the configured deadline on persistent server EOF without maintenance startup or publication", async t => {
  const fixture = redisRuntimeFixture(t, [{ code: 1, stdout: "", stderr: "Error: Server closed the connection" }], 2500);
  const receiptPath = join(fixture.fixture.stateDirectory, "deployment.json");
  const receiptBefore = readFileSync(receiptPath, "utf8");
  await fixture.runtime.config();
  const { calls, handlers } = actions();
  Object.assign(handlers, { ensureImages: fixture.runtime.ensureImages, ensureDatabase: fixture.runtime.ensureDatabase,
    recordFailure: fixture.runtime.recordFailure });
  await assert.rejects(executeLifecycle({ recreate: true }, handlers), /2\.5 seconds \(checking_redis\): Error: Server closed the connection/);
  assert.equal(fixture.elapsedMilliseconds(), 2500);
  assert.equal(fixture.probes.length, 3);
  assert.deepEqual(fixture.waits, [1000, 1000, 500]);
  assert.deepEqual(fixture.probes.map(probe => probe.options.timeout), [2500, 1500, 500]);
  assert(!calls.some(name => ["checkSchema", "backup", "migrate", "startServices", "commitDeployment"].includes(name)));
  assert.equal(readFileSync(receiptPath, "utf8"), receiptBefore);
  const operation = JSON.parse(readFileSync(join(fixture.fixture.stateDirectory, "operation.json"), "utf8"));
  assert.equal(operation.phase, "failed");
  assert.equal(operation.failed_phase, "checking_redis");
  assert(!Number.isNaN(Date.parse(operation.failed_at)));
  assert.match(operation.failure, /Error: Server closed the connection/);
});

for (const [name, reply] of [
  ["authentication", { code: 1, stdout: "NOAUTH Authentication required", stderr: "" }],
  ["configuration", { code: 1, stdout: "ERR unknown command redis-cli", stderr: "" }],
  ["terminal errors containing transient wording", { code: 1, stdout: "NOAUTH Authentication required before retrying a connection refused timeout", stderr: "" }],
  ["unexpected response", { code: 0, stdout: "OK", stderr: "" }],
  ["nonzero PONG", { code: 1, stdout: "PONG", stderr: "" }],
]) test(`CLI Redis readiness fails ${name} immediately with the actual reply`, async t => {
  const fixture = redisRuntimeFixture(t, [reply]);
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  await assert.rejects(fixture.runtime.ensureDatabase(), error => {
    assert(error.message.includes(reply.stdout));
    assert.match(error.message, /0\.0 seconds \(checking_redis\)/);
    return true;
  });
  assert.equal(fixture.probes.length, 1);
  assert.deepEqual(fixture.waits, []);
});

test("CLI Redis readiness redacts terminal errors in thrown and saved failure evidence", async t => {
  const fixture = redisRuntimeFixture(t, [{ code: 1, stdout: "WRONGPASS supplied canary-redis-credential was rejected", stderr: "" }]);
  const configuration = readFixtureJson(fixture.fixture, "fixture.json").config;
  writeFileSync(Object.values(configuration.secrets)[0].file, "canary-redis-credential");
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  let failure;
  try { await fixture.runtime.ensureDatabase(); } catch (error) { failure = error; }
  assert(failure);
  assert(!failure.message.includes("canary-redis-credential"));
  assert.match(failure.message, /WRONGPASS supplied \[redacted\]/);
  await fixture.runtime.recordFailure(failure);
  assert(!readFileSync(join(fixture.fixture.stateDirectory, "operation.json"), "utf8").includes("canary-redis-credential"));
});

test("CLI migration guard durability failure prevents migrations writer startup and deployment publication", async t => {
  let appliedSchema = 28;
  const { runtime, calls, fixture } = imageRuntimeFixture(t, "postgres:18.6-trixie", 18, false, args => {
    if (args.includes("--mount")) return "18";
    if (args.includes("ping")) return "PONG";
    if (args.includes("ps")) return "[]";
    if (args.includes("scripts/apply_postgres_migrations.py")) {
      if (!args.includes("--check")) { appliedSchema = 29; return ""; }
      return JSON.stringify({ initialized: true, roles_ready: true, credentials_ready: true, expected_version: 29,
        applied_versions: Array.from({ length: appliedSchema }, (_, index) => index + 1), pending_versions: appliedSchema === 28 ? [29] : [] });
    }
    if (args.includes("scripts/verify_postgres_cli_state.py")) return JSON.stringify({ reviews: { count: 1, digest: "original-H0" } });
  });
  rmSync(join(fixture.stateDirectory, "deployment.json"));
  await runtime.config();
  const guardPath = join(fixture.stateDirectory, "migration-guard.json");
  let failedDirectorySync = false;
  observeAtomicFileSystem(t, (event, events) => {
    if (!failedDirectorySync && event.directory && events.findLast(previous => previous.method === "renameSync")?.destination === guardPath) {
      failedDirectorySync = true; return true;
    }
    return false;
  });
  await assert.rejects(executeLifecycle({ recreate: true }, { ...runtime, verifyReady: async () => {} }), /deployment state.*durab/i);
  assert(failedDirectorySync, "failure occurs after the guard rename");
  assert.equal(appliedSchema, 28);
  assert(!calls.some(args => args.includes("scripts/apply_postgres_migrations.py") && !args.includes("--check")));
  assert(!calls.some(args => args.includes("up") && args.includes("foreground-worker")));
  assert(calls.at(-2).includes("stop") && calls.at(-2).includes("foreground-worker"), "failure leaves writers stopped");
  assert(!existsSync(join(fixture.stateDirectory, "deployment.json")));
  const guard = JSON.parse(readFileSync(guardPath, "utf8"));
  assert(guard.backup.verified);
  assert.equal(guard.study_invariants.reviews.digest, "original-H0");
  assert.equal(guard.state, "pending");
  assert.equal(JSON.parse(readFileSync(join(fixture.stateDirectory, "operation.json"), "utf8")).phase, "failed");
});

for (const [label, reference, major, fallback] of [
  ["standard compatible tag", "postgres:18.6-trixie", 18, false],
  ["registry-qualified incompatible tag", "docker.io/library/postgres:19", 19, false],
  ["digest-pinned incompatible image", `postgres@sha256:${"f".repeat(64)}`, 19, false],
  ["compatible tag with incompatible contents", "postgres:18.6-trixie", 19, false],
  ["compatible recorded fallback ID", "postgres:18.6-trixie", 18, true],
  ["incompatible recorded fallback ID", "postgres:18.6-trixie", 19, true],
]) test(`CLI PostgreSQL image major: ${label}`, async t => {
  const { runtime, calls } = imageRuntimeFixture(t, reference, major, fallback);
  await runtime.config();
  if (major === 18) { await runtime.ensureImages(); await runtime.ensureImages(); }
  else await assert.rejects(executeLifecycle({ recreate: true }, runtime), /Registered PostgreSQL major 18; candidate image PostgreSQL major 19.*separate major-upgrade/);
  const probe = calls.find(args => args.includes("--version"));
  assert(probe, "actual image contents must be checked");
  assert.equal(calls.filter(args => args.includes("--version")).length, 1, "immutable image version is cached within the invocation");
  assert(probe.includes("sha256:fixture-postgres"));
  assert(probe.includes("none") && probe.includes("--entrypoint") && probe.includes("postgres"));
  assert(!probe.includes("--mount") && !probe.includes("-v"));
  assert(!calls.some(args => args.includes("up") || args.includes("stop")));
});

test("CLI migration guard rejects wrong database identity and incompatible continuation schemas", async t => {
  const { runtime, fixture } = imageRuntimeFixture(t, "postgres:18.6-trixie", 18);
  await runtime.config();
  const original = { version: 1, target: "wrong-target", database: { name: "tempo", volume: "tempo-postgres-data" },
    starting_schema: 28, intended_schema: 30, starting_versions: Array.from({ length: 28 }, (_, i) => i + 1),
    study_invariants: { reviews: { columns: ["id"], count: 1, digest: "preserved" } },
    backup: { verified: true, filename: "original.dump" }, state: "pending" };
  atomicJson(join(fixture.stateDirectory, "migration-guard.json"), original);
  // The wrong identity must fail even with explicit authorization.
  const guarded = createRuntime(fixture.target, { run: async () => ({ stdout: JSON.stringify(readFixtureJson(fixture, "fixture.json").config) }),
    stateDirectory: fixture.stateDirectory, revision: "new", retry: true, log: () => {} });
  await guarded.config();
  assert.throws(() => guarded.checkMigrationRetry(), /another database/);
  assert.throws(() => guarded.resolveMigrationGuard(), /unresolved/);
  await assert.rejects(guarded.startServices(), /unresolved/);
  await assert.rejects(guarded.commitDeployment(), /unresolved/);
  original.target = targetKey(fixture.target);
  atomicJson(join(fixture.stateDirectory, "migration-guard.json"), original);
  const incompatible = createRuntime(fixture.target, { run: async (_command, args) => ({ stdout: JSON.stringify(args.includes("--check")
    ? { expected_version: 29, applied_versions: original.starting_versions, pending_versions: [29], initialized: true, roles_ready: true, credentials_ready: true }
    : readFixtureJson(fixture, "fixture.json").config) }), stateDirectory: fixture.stateDirectory, revision: "new", retry: true, log: () => {} });
  await incompatible.config();
  assert.equal(incompatible.checkMigrationRetry(), true);
  await assert.rejects(incompatible.checkSchema(), /cannot safely continue.*original.dump/);
});

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
    assert(result.stdout.includes("previous verified version ready, update deferred"));
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

test("actual CLI fallback corrects uncommitted or missing dependency containers before starting recorded applications", t => {
  for (const mutation of ["image", "config", "missing"]) {
    const fixture = commandFixture(t, "ci-fail");
    const machinePath = join(fixture.directory, "machine.json");
    const machine = JSON.parse(readFileSync(machinePath, "utf8"));
    if (mutation === "image") machine.containers.find(container => container.Id === "container-redis").Image = "sha256:uncommitted-candidate";
    if (mutation === "config") machine.containers.find(container => container.Id === "container-postgres").Config.Labels["com.docker.compose.config-hash"] = "uncommitted-config";
    if (mutation === "missing") machine.containers = machine.containers.filter(container => container.Id !== "container-redis");
    writeFileSync(machinePath, JSON.stringify(machine));
    const result = fixture.command("start", "--no-open");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert(result.stdout.includes("previous verified version ready, update deferred"));
    const calls = fixture.calls();
    const shutdown = calls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
    const dependencies = calls.findIndex(call => call.args.includes("up") && call.args.includes("postgres"));
    assert(shutdown >= 0 && dependencies > shutdown, mutation + " must stop writers before correction");
    assert(!calls[dependencies].args.includes("--no-recreate"));
    const corrected = JSON.parse(readFileSync(machinePath, "utf8"));
    for (const name of ["postgres", "redis"]) {
      const container = corrected.containers.find(container => container.Config.Labels["com.docker.compose.service"] === name);
      assert.equal(container.Image, `sha256:fixture-${name}`);
      assert.equal(container.Config.Labels["com.docker.compose.config-hash"], `fixture-hash-${name}`);
    }
    assert(!calls.some(call => call.args.includes("build") || call.args.some(arg => arg.includes("pg_dump") || arg.includes("pg_restore"))));
  }
});

test("actual CLI compatible fallback trusts immutable dependency IDs rather than mutable image tags", t => {
  const fixture = commandFixture(t, "ci-fail");
  const result = fixture.command("start", "--no-open");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const calls = fixture.calls();
  assert(!calls.some(call => call.args.includes("stop") || call.args.includes("build")));
  const dependencies = calls.find(call => call.args.includes("up") && call.args.includes("postgres"));
  assert(dependencies.args.includes("--no-recreate"));
});

function interruptedApplicationRollout(t, partial = false) {
  const fixture = commandFixture(t, "upgrade");
  const receiptPath = join(fixture.stateDirectory, "deployment.json");
  const receipt = readFixtureJson(fixture, "deployment.json", true);
  const configuration = readFixtureJson(fixture, "fixture.json").config;
  // A and B use identical dependencies. Only application identity can reveal
  // the interrupted rollout; dependency correction must not mask this bug.
  for (const name of ["postgres", "redis"]) receipt.images[name] = `sha256:${configuration.services[name].image}`;
  writeFileSync(receiptPath, JSON.stringify(receipt));
  editFixtureJson(fixture, "recorded-images.json", overlay => {
    for (const name of ["postgres", "redis"]) overlay.services[name].image = receipt.images[name];
  });
  editFixtureJson(fixture, "machine.json", machine => {
    for (const container of machine.containers) {
      const name = container.Config.Labels["com.docker.compose.service"];
      if (["postgres", "redis"].includes(name)) container.Image = receipt.images[name];
    }
  });
  editFixtureJson(fixture, "fixture.json", value => { value.mode = partial ? "interrupted-workers" : "interrupted-applications"; });
  const killed = fixture.command("start", "--no-open");
  assert.equal(killed.signal, "SIGKILL", killed.stdout + killed.stderr);
  assert.equal(readFileSync(receiptPath, "utf8"), JSON.stringify(receipt));
  const machine = readFixtureJson(fixture, "machine.json");
  assert.equal(machine.schema, 29);
  assert.equal(assertOriginalHistory(fixture).state, "verified");
  for (const name of ["api", "foreground-worker", "background-worker"]) {
    assert(machine.running.includes(name));
    assert.notEqual(machine.containers.find(container => container.Id === `container-${name}`).Image, receipt.images[name]);
  }
  assert.equal(machine.running.includes("web"), !partial);
  editFixtureJson(fixture, "fixture.json", value => { value.mode = "ci-fail"; });
  return fixture;
}

function assertIncompatibleRecovery(fixture, shouldStop) {
  const receiptPath = join(fixture.stateDirectory, "deployment.json"), guardPath = join(fixture.stateDirectory, "migration-guard.json");
  const receipt = readFileSync(receiptPath, "utf8"), guard = readFileSync(guardPath, "utf8");
  const before = readFixtureJson(fixture, "machine.json"), callStart = fixture.calls().length;
  const result = fixture.command("start", "--no-open");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /previous deployment is incompatible with the current database/);
  const calls = fixture.calls().slice(callStart);
  const stopped = calls.findIndex(call => call.args.includes("stop") && call.args.includes("foreground-worker"));
  const schemaCheck = calls.findIndex(call => call.args.includes("scripts/apply_postgres_migrations.py") && call.args.includes("--check"));
  if (shouldStop) assert(stopped >= 0 && stopped < schemaCheck, "uncommitted writers must stop before incompatible schema rejection");
  else assert.equal(stopped, -1, "stopped containers do not require another shutdown");
  assert(!calls.some(call => call.args.includes("up") && call.args.includes("api")), "incompatible applications cannot start");
  assert(!calls.some(call => call.args.includes("build") || call.args.includes("pull")
    || (call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check"))
    || call.args.some(argument => argument.includes("pg_dump") || argument.includes("pg_restore"))));
  const dependencies = calls.find(call => call.args.includes("up") && call.args.includes("postgres"));
  assert(dependencies.args.includes("--no-recreate"), "matching dependencies remain intact");
  const after = readFixtureJson(fixture, "machine.json");
  assert.deepEqual(after.running.sort(), ["postgres", "redis"]);
  assert.equal(after.schema, before.schema);
  assert.equal(after.history, before.history);
  assert.equal(after.migrations, before.migrations);
  assert.deepEqual(after.migrationVersions, before.migrationVersions);
  assert.equal(readFileSync(receiptPath, "utf8"), receipt);
  assert.equal(readFileSync(guardPath, "utf8"), guard);
  const failure = readFixtureJson(fixture, "operation.json", true);
  assert.equal(failure.phase, "failed");
  assert.match(failure.failure, /incompatible/);
  assert(readdirSync(fixture.stateDirectory).some(name => name.startsWith("failure-")));
}

test("actual CLI interrupted fallback stops uncommitted candidate writers before rejecting an incompatible schema", t => {
  assertIncompatibleRecovery(interruptedApplicationRollout(t), true);
});

test("actual CLI interrupted fallback quiesces a partially started candidate", t => {
  assertIncompatibleRecovery(interruptedApplicationRollout(t, true), true);
});

test("actual CLI interrupted fallback stops mixed application identities", t => {
  const fixture = interruptedApplicationRollout(t);
  const receipt = readFixtureJson(fixture, "deployment.json", true);
  editFixtureJson(fixture, "machine.json", machine => {
    machine.containers.find(container => container.Id === "container-api").Image = receipt.images.api;
  });
  assertIncompatibleRecovery(fixture, true);
});

test("actual CLI interrupted fallback rejects application config drift despite matching immutable images", t => {
  const fixture = interruptedApplicationRollout(t);
  const receipt = readFixtureJson(fixture, "deployment.json", true);
  editFixtureJson(fixture, "machine.json", machine => {
    for (const container of machine.containers) container.Image = receipt.images[container.Config.Labels["com.docker.compose.service"]];
    machine.containers.find(container => container.Id === "container-foreground-worker").Config.Labels["com.docker.compose.config-hash"] = "candidate-config";
  });
  assertIncompatibleRecovery(fixture, true);
});

test("actual CLI interrupted fallback rejects incompatible schema cleanly with no running applications", t => {
  const fixture = interruptedApplicationRollout(t);
  editFixtureJson(fixture, "machine.json", machine => { machine.running = ["postgres", "redis"]; });
  assertIncompatibleRecovery(fixture, false);
});

test("actual CLI compatible fallback keeps committed applications running despite a stale rollout journal", t => {
  const fixture = commandFixture(t, "ci-fail");
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ revision: "c".repeat(40), phase: "checking_readiness" }));
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  const before = readFixtureJson(fixture, "machine.json");
  const result = fixture.command("start", "--no-open");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert(!fixture.calls().some(call => call.args.includes("stop") || call.args.includes("build") || call.args.includes("pull")
    || (call.args.includes("scripts/apply_postgres_migrations.py") && !call.args.includes("--check"))));
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assert.deepEqual(readFixtureJson(fixture, "machine.json").containers.map(container => container.Image), before.containers.map(container => container.Image));
});

test("actual CLI concurrent source changes cancel without fast-forward or restarting fallback", t => {
  for (const mode of ["race-dirty", "race-head"]) {
    const fixture = commandFixture(t, mode);
    const result = fixture.command("start", "--no-open");
    assert.notEqual(result.status, 0, result.stdout + result.stderr);
    assert(result.stderr.includes("source changed"));
    assert(!fixture.calls().some(call => call.args.includes("merge") || call.args.includes("build") || call.args.includes("up") || call.args.includes("stop")));
    const machine = JSON.parse(readFileSync(join(fixture.directory, "machine.json"), "utf8"));
    assert.equal(machine.head, (mode === "race-head" ? "d" : "c").repeat(40));
    if (mode === "race-dirty") { assert(machine.sourceEdited); assert.equal(readFileSync(join(fixture.root, "personal-work"), "utf8"), "preserved study notes"); }
  }
});

function sourceSelectionFixture({ changedSource, runs = [{ id: 1 }], failedRuns = [], incompleteRuns = [] } = {}) {
  const current = "c".repeat(40), revision = "a".repeat(40), calls = [];
  let head = current, branch = "main", dirty = false;
  const run = async (_command, args) => {
    calls.push(args);
    if (args[0] === "--no-optional-locks") args = args.slice(1);
    let stdout = "";
    if (args[0] === "branch") stdout = branch;
    if (args[0] === "status") stdout = dirty ? "?? user-work" : "";
    if (args[0] === "remote") stdout = "https://github.com/aaweaver-actuary/tempo";
    if (args[0] === "rev-parse") stdout = args[1] === "HEAD" ? head : revision;
    if (args[0] === "merge") { head = args.at(-1); stdout = head; }
    return { stdout, code: 0 };
  };
  const fetchJson = async path => {
    if (path.includes("workflows/")) return { workflow_runs: runs.map(candidate => ({ head_sha: revision, head_branch: "main", event: "push", status: "completed", html_url: "fixture", ...candidate })) };
    if (changedSource === "dirty") dirty = true;
    if (changedSource === "head") head = "d".repeat(40);
    if (changedSource === "branch") branch = "personal-work";
    const id = Number(path.match(/runs\/(\d+)/)[1]);
    return { jobs: ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify", "lifecycle / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed", conclusion: failedRuns.includes(id) && name === "quality" ? "failure" : "success" })).filter(job => !incompleteRuns.includes(id) || job.name !== "browser / verify") };
  };
  return { run, fetchJson, calls, revision, source: () => ({ head, branch, dirty }) };
}

for (const changedSource of ["dirty", "head", "branch"]) test(`source update refuses concurrent ${changedSource} changes immediately before fast-forward`, async () => {
  const fixture = sourceSelectionFixture({ changedSource });
  await assert.rejects(selectCandidate({ root: "fixture" }, fixture.run, fixture.fetchJson), /source changed/i);
  assert(!fixture.calls.some(args => args[0] === "merge"));
  assert.equal(fixture.source().head, (changedSource === "head" ? "d" : "c").repeat(40));
  if (changedSource === "dirty") assert(fixture.source().dirty);
  if (changedSource === "branch") assert.equal(fixture.source().branch, "personal-work");
});

test("source update accepts a complete successful exact main run after a failed exact run", async () => {
  const fixture = sourceSelectionFixture({ runs: [{ id: 1 }, { id: 2 }], failedRuns: [1] });
  const selected = await selectCandidate({ root: "fixture" }, fixture.run, fixture.fetchJson);
  assert.equal(selected.evidence.run_id, 2);
});

test("source update rejects exact main revisions when every eligible run fails or lacks required jobs", async () => {
  const fixture = sourceSelectionFixture({ runs: [{ id: 1 }, { id: 2 }], failedRuns: [1], incompleteRuns: [2] });
  await assert.rejects(selectCandidate({ root: "fixture" }, fixture.run, fixture.fetchJson), /quality/);
  assert(!fixture.calls.some(args => args[0] === "merge"));
});

test("source update never accepts successful CI from another SHA branch or event", async () => {
  for (const candidate of [{ head_sha: "b".repeat(40) }, { head_branch: "personal" }, { event: "pull_request" }]) {
    const fixture = sourceSelectionFixture({ runs: [{ id: 1 }, { id: 2, ...candidate }], failedRuns: [1] });
    await assert.rejects(selectCandidate({ root: "fixture" }, fixture.run, fixture.fetchJson));
    assert(!fixture.calls.some(args => args[0] === "merge"));
  }
});


test("source update accepts successful exact main evidence after a pending run and across workflow pages", async () => {
  const fixture = sourceSelectionFixture();
  const fetchJson = async path => {
    if (path.includes("workflows/")) return { workflow_runs: path.endsWith("page=1")
      ? Array.from({ length: 100 }, (_, index) => ({ id: index, head_sha: fixture.revision, head_branch: "main", event: "push", status: "in_progress" }))
      : [{ id: 101, head_sha: fixture.revision, head_branch: "main", event: "push", status: "completed", html_url: "fixture" }] };
    return fixture.fetchJson(path);
  };
  assert.equal((await selectCandidate({ root: "fixture" }, fixture.run, fetchJson)).evidence.run_id, 101);
});

test("actual CLI backup rejects mismatched dependencies without starting any recorded application", t => {
  const fixture = commandFixture(t, "ci-fail");
  const machinePath = join(fixture.directory, "machine.json");
  const machine = JSON.parse(readFileSync(machinePath, "utf8"));
  machine.containers.find(container => container.Id === "container-redis").Image = "sha256:uncommitted-candidate";
  writeFileSync(machinePath, JSON.stringify(machine));
  const result = fixture.command("backup");
  assert.notEqual(result.status, 0);
  assert(result.stderr.includes("Run tempo start"));
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop")));
});

function diagnosticFixture(t, { receipt = false, diagnostics = {}, machine = {} } = {}) {
  const command = commandFixture(t);
  const fixturePath = join(command.directory, "fixture.json");
  writeFileSync(fixturePath, JSON.stringify({ ...JSON.parse(readFileSync(fixturePath, "utf8")), diagnostics }));
  const machinePath = join(command.directory, "machine.json");
  writeFileSync(machinePath, JSON.stringify({ ...JSON.parse(readFileSync(machinePath, "utf8")), ...machine }));
  if (!receipt) rmSync(join(command.stateDirectory, "deployment.json"));
  return command;
}

const diagnosticMainRun = { id: 12, head_sha: "a".repeat(40), head_branch: "main", event: "push",
  status: "completed", html_url: "https://github.com/fixture/ci" };

function failFixtureSourceProbe(fixture, probe, once = false) {
  if (probe === "launch") {
    // No host Git fallback: the absolute Node shebang still runs fake Docker.
    fixture.environment.PATH = join(fixture.directory, "bin");
    fs.chmodSync(join(fixture.directory, "bin/git"), 0o644);
  } else editFixtureJson(fixture, "machine.json", machine => {
    machine.gitProbeFailure = { probe, once, message: "fatal: cannot read repository metadata: canary-private-password" };
  });
}

function sourceFallbackFixture(t, probe, once = false) {
  const fixture = diagnosticFixture(t, { receipt: true, machine: { schema: 29 } });
  editFixtureJson(fixture, "deployment.json", receipt => { receipt.schema = 29; }, true);
  writeFileSync(join(fixture.root, "study-notes"), "preserve local study work");
  failFixtureSourceProbe(fixture, probe, once);
  return fixture;
}

function assertNoCandidateMutation(fixture) {
  for (const call of fixture.calls()) {
    assert(!(call.command === "git" && ["fetch", "merge", "reset", "checkout", "switch"].some(argument => call.args.includes(argument))), JSON.stringify(call));
    assert(!call.args.includes("build"), JSON.stringify(call));
  }
}

for (const probe of ["branch", "head", "status", "remote", "launch"]) test(`actual CLI source inspection fallback survives ${probe} probe failure without candidate mutation`, t => {
  const fixture = sourceFallbackFixture(t, probe);
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  const sourceFiles = [join(fixture.root, "study-notes"), join(fixture.root, "backend/app/schema_version.py"), ...fixture.target.composeFiles];
  const sourceBefore = sourceFiles.map(path => readFileSync(path, "utf8"));
  const sourceHead = readFixtureJson(fixture, "machine.json").head;
  const result = fixture.command("start", "--no-wait", "--no-open");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Local source state could not be inspected safely/);
  assert.match(result.stdout, /Attempting previously verified revision/);
  assert.match(result.stdout, /previous verified version ready, update deferred/);
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assert.deepEqual(sourceFiles.map(path => readFileSync(path, "utf8")), sourceBefore);
  assert.equal(readFixtureJson(fixture, "machine.json").head, sourceHead);
  assert.equal(readFixtureJson(fixture, "operation.json", true).phase, "ready_previous_version");
  assertNoCandidateMutation(fixture);
  const requests = readFileSync(join(fixture.directory, "requests.jsonl"), "utf8").trim().split("\n").map(JSON.parse);
  assert(!requests.some(request => request.url.includes("api.github.com")), "unknown source must not enter candidate verification; readiness still runs");
});

test("actual CLI source inspection fallback stays deferred when the failed probe recovers", t => {
  const fixture = sourceFallbackFixture(t, "branch", true);
  const result = fixture.command("restart", "--no-wait", "--no-open");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /previous verified version ready, update deferred/);
  assertNoCandidateMutation(fixture);
});

test("actual CLI source inspection no-receipt startup fails closed without changing services data or source", t => {
  const fixture = diagnosticFixture(t);
  failFixtureSourceProbe(fixture, "head");
  const machine = readFileSync(join(fixture.directory, "machine.json"), "utf8");
  const result = fixture.command("start", "--no-wait", "--no-open");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /Local source state could not be inspected safely/);
  assert.match(result.stderr, /no verified fallback/);
  assert.equal(readFileSync(join(fixture.directory, "machine.json"), "utf8"), machine);
  assert.deepEqual(readdirSync(fixture.stateDirectory), []);
  assertNoCandidateMutation(fixture);
  assert(!fixture.calls().some(call => ["up", "stop", "run", "pull"].some(argument => call.args.includes(argument))));
});

test("actual CLI source inspection migrate never substitutes the recorded fallback", t => {
  const fixture = sourceFallbackFixture(t, "branch");
  const result = fixture.command("migrate", "--no-wait");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /Local source state could not be inspected safely/);
  assert(!fixture.calls().some(call => ["up", "stop", "run", "build"].some(argument => call.args.includes(argument))));
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
});

for (const surface of ["doctor", "status", "start", "restart", "migrate"]) test(`actual CLI source inspection diagnostics remain read-only for ${surface}`, t => {
  const fixture = sourceFallbackFixture(t, "status");
  const paths = [join(fixture.directory, "machine.json"), join(fixture.stateDirectory, "deployment.json")];
  const before = paths.map(path => readFileSync(path, "utf8"));
  const argumentsList = [surface, ...(["start", "restart", "migrate"].includes(surface) ? ["--plan"] : [])];
  const concise = fixture.command(...argumentsList);
  const verbose = fixture.command(...argumentsList, "--verbose");
  for (const result of [concise, verbose]) {
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Local source state could not be inspected safely/);
    assert.match(result.stdout, /repair Git\/repository access/i);
    assert.equal((result.stdout.match(/Next:|Run:/g) ?? []).length, 1);
    assert(!result.stdout.includes("canary-private-password"));
  }
  assert(!concise.stdout.includes("fatal: cannot read repository metadata"));
  assert.match(verbose.stdout, /fatal: cannot read repository metadata.*\[redacted\]/);
  assert.match(verbose.stdout, /working-tree status unavailable/);
  assert.doesNotMatch(verbose.stdout, /Local checkout:.*; clean/);
  assert.match(verbose.stdout, /Verified deployment: b{40}/);
  assert.match(verbose.stdout, /Running application revision:/);
  assert.match(verbose.stdout, /Applied schema versions: 1, 2/);
  assert.match(verbose.stdout, /Maintenance: idle/);
  assert.match(verbose.stdout, /Verification: verified/);
  if (surface === "doctor") assert.match(verbose.stdout, /API health: HTTP 200/);
  assert.deepEqual(paths.map(path => readFileSync(path, "utf8")), before);
  assert.deepEqual(readdirSync(fixture.stateDirectory), ["deployment.json"]);
  assertNoCandidateMutation(fixture);
  assert(!fixture.calls().some(call => ["up", "stop", "run", "pull"].some(argument => call.args.includes(argument))));
});

test("actual CLI source inspection diagnostics retain independent facts when Git cannot launch or local schema is unreadable", t => {
  const fixture = sourceFallbackFixture(t, "launch");
  rmSync(join(fixture.root, "backend/app/schema_version.py"));
  const result = fixture.command("doctor", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /unknown branch.*unknown revision.*working-tree status unavailable/);
  assert.match(result.stdout, /Git probe.*EACCES/);
  assert.match(result.stdout, /Required local schema: unavailable/);
  assert.match(result.stdout, /Applied schema versions: 1, 2/);
  assert.match(result.stdout, /Pending local migrations: unknown/);
  assert.match(result.stdout, /API health: HTTP 200/);
  assertNoCandidateMutation(fixture);
});

for (const unsafeFallback of ["images", "schema", "migration"]) test(`actual CLI source inspection safety still rejects unsafe fallback ${unsafeFallback}`, t => {
  const fixture = sourceFallbackFixture(t, "branch");
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  if (unsafeFallback === "images") editFixtureJson(fixture, "machine.json", machine => { machine.imageInspectionUnavailable = true; });
  if (unsafeFallback === "schema") editFixtureJson(fixture, "machine.json", machine => { machine.schema = 30; });
  if (unsafeFallback === "migration") atomicJson(join(fixture.stateDirectory, "migration-guard.json"), { state: "pending" });
  const result = fixture.command("start", "--no-wait", "--no-open");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.doesNotMatch(result.stdout, /Tempo ready/);
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assertNoCandidateMutation(fixture);
  assert(!fixture.calls().some(call => call.args.includes("up") && call.args.includes("api")));
});

const inspectedSourceState = { branch: "main", head: "a".repeat(40), changes: "", origin: "https://github.com/aaweaver-actuary/tempo" };

function sourceProbeFixture(failedProbe, failure) {
  const calls = [];
  const run = async (_command, argumentsList, settings) => {
    calls.push(argumentsList);
    const args = argumentsList[0] === "--no-optional-locks" ? argumentsList.slice(1) : argumentsList;
    const field = { branch: "branch", "rev-parse": "head", status: "changes", remote: "origin" }[args[0]];
    assert(field, `Unexpected mutation or network probe: ${argumentsList}`);
    assert.equal(settings.allowFailure, true);
    if (field === failedProbe) {
      if (failure) throw failure;
      return { code: 128, stdout: "", stderr: "fatal: repository metadata unreadable" };
    }
    return { code: 0, stdout: inspectedSourceState[field], stderr: "" };
  };
  return { run, calls };
}

test("CLI source inspection retains known facts and classifies only operational Git failures", async () => {
  for (const failedProbe of ["branch", "head", "changes", "origin"]) {
    const fixture = sourceProbeFixture(failedProbe);
    const source = await inspectCandidateSource({ root: "fixture" }, fixture.run);
    assert.equal(source.problem.code, "source_unavailable");
    assert.equal(source[failedProbe], null);
    assert.equal(source.failures.length, 1);
    assert.equal(fixture.calls.length, 4);
    for (const field of Object.keys(inspectedSourceState).filter(field => field !== failedProbe)) assert.equal(source[field], inspectedSourceState[field]);
    assert.equal(sourceFingerprint(source), null);
    assert(isCandidateBlocker(source.problem));
  }
  for (const code of ["ENOENT", "EACCES", "EPERM", "ENOTDIR", "EIO"]) {
    const cause = Object.assign(new Error("repository access failed"), { code });
    const error = new CommandExecutionError("git", "Git cannot launch", { cause });
    const source = await inspectCandidateSource({ root: "fixture" }, sourceProbeFixture("head", error).run);
    assert.equal(source.problem.code, "source_unavailable");
    assert.equal(source.problem.cause, error);
    assert.equal(source.head, null);
  }
});

test("CLI source inspection preserves programming errors and cancellation instead of permitting fallback", async () => {
  for (const error of [new TypeError("invalid internal state"), new Error("git failed: looks operational but has no provenance"),
    new CommandExecutionError("git", "unknown spawn error", { cause: Object.assign(new Error("bug"), { code: "ERR_INTERNAL" }) }),
    new CommandExecutionError("git", "cancelled", { cause: Object.assign(new Error("cancelled"), { code: "ABORT_ERR" }) })]) {
    await assert.rejects(inspectCandidateSource({ root: "fixture" }, sourceProbeFixture("head", error).run), actual => actual === error);
    assert(!isCandidateBlocker(error));
  }
  const cancellation = new AbortController(); cancellation.abort();
  await assert.rejects(inspectCandidateSource({ root: "fixture" }, () => assert.fail("cancelled source inspection cannot probe"),
    { signal: cancellation.signal }), error => error === cancellation.signal.reason);
  const abortedProcess = new CommandExecutionError("git", "request aborted", { cause: Object.assign(new Error("abort"), { code: "ABORT_ERR" }) });
  assert(isOperationalGitFailure(abortedProcess, cancellation.signal));
  assert(!isOperationalGitFailure(new TypeError("bug after a deadline"), cancellation.signal));
  const internalError = new TypeError("remote probe programming error");
  const fixture = sourceProbeFixture();
  await assert.rejects(assessCandidate({ root: "fixture" }, (command, args, settings) => args[0] === "ls-remote"
    ? Promise.reject(internalError) : fixture.run(command, args, settings)), error => error === internalError);
});

test("CLI source inspection executor retains native process launch provenance and legacy exit behavior", async t => {
  const root = directory(t);
  const run = commandExecutor({ root, environment: { PATH: root }, output: () => {} });
  await assert.rejects(run("git", ["branch", "--show-current"], { allowFailure: true }), error => {
    assert(error instanceof CommandExecutionError);
    assert.equal(error.cause.code, "ENOENT");
    assert.equal(error.command, "git");
    assert.equal(error.exitCode, undefined, "child status cannot replace the CLI's existing failure exit code");
    return isCandidateBlocker(error);
  });
});

test("CLI source inspection evidence bounds probe failures and redacts repository URL credentials", async () => {
  const message = `${"x".repeat(7000)} https://private-user:unlisted-password@github.com/aaweaver-actuary/tempo Bearer private-token`;
  for (const failure of [null, new CommandExecutionError("git", message, { cause: Object.assign(new Error("launch failed"), { code: "EACCES" }) })]) {
    const fixture = sourceProbeFixture("head", failure);
    const source = await inspectCandidateSource({ root: "fixture" }, (command, args, settings) => args[0] === "rev-parse" && !failure
      ? Promise.resolve({ code: 128, stdout: "", stderr: message }) : fixture.run(command, args, settings));
    assert.equal(source.problem.code, "source_unavailable");
    assert(source.failures[0].message.length < 4050);
    assert.match(source.failures[0].message, /https:\/\/\[redacted\]@github.com/);
    assert.doesNotMatch(source.failures[0].message, /private-user|unlisted-password|private-token/);
  }
});

test("CLI source fingerprint compares deliberate Git state and ignores diagnostic problem metadata", async () => {
  const { TempoProblem } = await guidance();
  const sameState = { origin: inspectedSourceState.origin, changes: "", head: inspectedSourceState.head, branch: "main",
    problem: new TempoProblem("source_changes", "diagnostic metadata"), failures: [{ message: "different evidence" }] };
  assert.deepEqual(sourceFingerprint(sameState), inspectedSourceState);
  assert(sourceMatchesFingerprint(sameState, inspectedSourceState));
  for (const field of Object.keys(inspectedSourceState)) {
    assert(!sourceMatchesFingerprint({ ...sameState, [field]: `${sameState[field]}-changed` }, inspectedSourceState), field);
    assert.equal(sourceFingerprint({ ...sameState, [field]: null }), null, field);
    assert(!sourceMatchesFingerprint({ ...sameState, [field]: null }, inspectedSourceState), field);
  }
});

test("CLI source inspection prevents candidate assessment waiting and selection mutations with unavailable probes", async () => {
  const { waitForVerification } = await guidance();
  for (const failedProbe of ["branch", "head", "changes", "origin"]) {
    const selection = sourceProbeFixture(failedProbe);
    await assert.rejects(selectCandidate({ root: "fixture" }, selection.run), error => error.code === "source_unavailable");
    assert.equal(selection.calls.length, 4);
    const assessment = sourceProbeFixture(failedProbe);
    await assert.rejects(waitForVerification({ assess: () => assessCandidate({ root: "fixture" }, assessment.run),
      wait: () => assert.fail("local source failure cannot wait on release verification") }), error => error.code === "source_unavailable");
    assert.equal(assessment.calls.length, 4);
  }
  const selection = sourceProbeFixture();
  await assert.rejects(selectCandidate({ root: "fixture" }, selection.run, undefined,
    { expectedSource: { ...inspectedSourceState, head: "b".repeat(40) } }), error => error.code === "source_changed");
  assert.equal(selection.calls.length, 4, "a stale fence must block even fetch");
});

function validDiagnosticMigrationGuard(fixture, state = "pending") {
  return { version: 1, target: targetKey(fixture.target),
    database: { name: "tempo", volume: "tempo-postgres-data" }, state,
    origin_revision: "b".repeat(40), starting_schema: 28, intended_schema: 29,
    starting_versions: Array.from({ length: 28 }, (_, index) => index + 1),
    study_invariants: { reviews: { columns: ["id", "rating"], count: 1, digest: "preserved" } },
    backup: { verified: true, filename: "original.dump" } };
}

function mutationFreeMigrationDiagnostics(fixture) {
  const backupDirectory = join(fixture.directory, "original-backups");
  mkdirSync(backupDirectory);
  writeFileSync(join(backupDirectory, "original.dump"), "original study backup");
  writeFileSync(join(backupDirectory, "original.dump.sha256"), "original backup checksum");
  writeFileSync(join(fixture.root, "study-notes"), "preserved operator work");
  const snapshotDirectory = path => readdirSync(path, { withFileTypes: true }).sort((left, right) => left.name.localeCompare(right.name))
    .map(entry => [entry.name, entry.isDirectory() ? snapshotDirectory(join(path, entry.name)) : readFileSync(join(path, entry.name), "utf8")]);
  const snapshot = () => [fixture.root, fixture.stateDirectory, backupDirectory].map(snapshotDirectory)
    .concat([fixture.registration, join(fixture.directory, "machine.json")].map(path => readFileSync(path, "utf8")));
  const original = snapshot();
  return (...argumentsList) => {
    const result = fixture.command(...argumentsList, "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.deepEqual(snapshot(), original, "journal, guard, receipt, backups, database, services and source remain byte-for-byte unchanged");
    for (const call of fixture.calls()) assert(!["fetch", "merge", "reset", "checkout", "switch", "build", "pull", "run", "up", "stop", "start", "restart", "rm", "down"]
      .some(argument => call.args.includes(argument)), JSON.stringify(call));
    for (const call of fixture.calls().filter(call => call.args.includes("psql"))) {
      assert(call.args.some(argument => argument.includes("default_transaction_read_only=on")));
      assert(call.args.some(argument => argument.includes("statement_timeout=1000")));
      assert(call.args.some(argument => argument.includes("lock_timeout=100")));
      assert.equal(call.args.at(-1), "SELECT version FROM tempo_schema_migrations ORDER BY version");
    }
    for (const request of readFileSync(join(fixture.directory, "requests.jsonl"), "utf8").trim().split("\n").map(JSON.parse)) {
      assert.equal(request.method, "GET");
      assert(request.url.includes("api.github.com") || request.url.endsWith("/api/health"));
    }
    return result.stdout;
  };
}

const migrationDiagnosticSurfaces = [["status"], ["doctor"], ["start", "--plan"], ["restart", "--plan"],
  ["migrate", "--plan"], ["migrate", "--retry", "--plan"]];

test("actual CLI migration diagnostics applying journal without a durable guard blocks every read-only surface", t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "applying_migrations" }));
  const diagnose = mutationFreeMigrationDiagnostics(fixture);
  for (const argumentsList of migrationDiagnosticSurfaces) {
    const output = diagnose(...argumentsList);
    assert.match(output, /Update eligibility: blocked/);
    assert.doesNotMatch(output, /Update eligibility: eligible|recovery attempt.*permitted/i);
    assert.match(output, /migration.*(?:failed|interrupted).*without a durable original guard/i);
    assert.match(output, /Preserve.*original backup.*operation/);
    assert.match(output, /inspect.*original history.*tempo migrate --retry/i);
  }
});

test("actual CLI migration diagnostics failed migration journal without a durable guard blocks every read-only surface", t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "failed", failed_phase: "applying_migrations", failure: "original migration failure" }));
  const diagnose = mutationFreeMigrationDiagnostics(fixture);
  for (const argumentsList of migrationDiagnosticSurfaces) {
    const output = diagnose(...argumentsList);
    assert.match(output, /Update eligibility: blocked/);
    assert.doesNotMatch(output, /Update eligibility: eligible|recovery attempt.*permitted/i);
    assert.match(output, /migration.*(?:failed|interrupted).*without a durable original guard/i);
    assert.match(output, /Preserve.*original backup.*operation/);
    assert.match(output, /inspect.*original history.*tempo migrate --retry/i);
  }
});

test("actual CLI migration diagnostics invalid target and database guards remain blocked even with retry", t => {
  for (const invalid of [{ target: "wrong-target" }, { database: { name: "another-database", volume: "tempo-postgres-data" } },
    { database: { name: "tempo", volume: "another-volume" } }]) {
    const fixture = diagnosticFixture(t, { receipt: true });
    writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify({ ...validDiagnosticMigrationGuard(fixture), ...invalid }));
    const diagnose = mutationFreeMigrationDiagnostics(fixture);
    for (const argumentsList of [["status"], ["doctor"], ["migrate", "--retry", "--plan"]]) {
      const output = diagnose(...argumentsList);
      assert.match(output, /Update eligibility: blocked.*Migration guard is invalid or belongs to another database/);
      assert.match(output, /Preserve the guard and backup.*inspect tempo doctor before recovery/);
      assert.doesNotMatch(output, /Update eligibility: eligible|recovery attempt.*permitted/i);
    }
  }
});

test("actual CLI migration diagnostics verified guards cannot bypass structural or target validation", t => {
  for (const invalid of [{ target: "wrong-target" }, { starting_versions: [28] }, { backup: { filename: "original.dump", verified: false } }]) {
    const fixture = diagnosticFixture(t, { receipt: true });
    writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify({ ...validDiagnosticMigrationGuard(fixture, "verified"), ...invalid }));
    const diagnose = mutationFreeMigrationDiagnostics(fixture);
    for (const argumentsList of [["doctor"], ["migrate", "--retry", "--plan"]]) {
      const output = diagnose(...argumentsList);
      assert.match(output, /Update eligibility: blocked.*Migration guard is invalid or belongs to another database/);
      assert.doesNotMatch(output, /Update eligibility: eligible|recovery attempt.*permitted/i);
    }
  }
});

test("actual CLI migration diagnostics pending original verification requires an explicit inspected retry", t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify(validDiagnosticMigrationGuard(fixture)));
  const diagnose = mutationFreeMigrationDiagnostics(fixture);
  for (const argumentsList of migrationDiagnosticSurfaces.filter(argumentsList => !argumentsList.includes("--retry"))) {
    const output = diagnose(...argumentsList);
    assert.match(output, /Update eligibility: blocked/);
    assert.match(output, /Original migration verification remains required/);
    assert.match(output, /original backup original.dump.*tempo migrate --retry/);
    assert.doesNotMatch(output, /Update eligibility: eligible|recovery attempt.*permitted/i);
  }
});

test("actual CLI migration diagnostics retry plan permits only an attempt while history and startup remain unresolved", t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify(validDiagnosticMigrationGuard(fixture)));
  const output = mutationFreeMigrationDiagnostics(fixture)("migrate", "--retry", "--plan");
  assert.match(output, /Explicit migration recovery attempt: permitted.*planned/);
  assert.match(output, /original.history verification remains unresolved.*lifecycle.*success/i);
  assert.match(output, /Ordinary startup safety: not established/);
  assert.match(output, /Update eligibility: blocked/);
  assert.doesNotMatch(output, /Update eligibility: eligible|recovery (?:has )?succeeded/i);
});

test("actual CLI migration diagnostics normal absent or verified guards retain update eligibility", t => {
  for (const guardState of [null, "verified"]) {
    const fixture = diagnosticFixture(t, { receipt: true });
    writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "ready" }));
    if (guardState) writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify(validDiagnosticMigrationGuard(fixture, guardState)));
    const diagnose = mutationFreeMigrationDiagnostics(fixture);
    for (const argumentsList of [["status"], ["doctor"], ["start", "--plan"]]) {
      const output = diagnose(...argumentsList);
      assert.match(output, /Verification: verified/);
      assert.match(output, /Update eligibility: eligible/);
      assert.doesNotMatch(output, /without a durable original guard|Original migration verification remains required|Ordinary startup safety: not established/);
    }
  }
});

test("CLI migration recovery assessment preserves lifecycle validation for every original guard field", async t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  const configuration = readFixtureJson(fixture, "fixture.json").config;
  const original = validDiagnosticMigrationGuard(fixture, "verified");
  const invalidGuards = [{ version: 2 }, { target: "wrong-target" },
    { database: { ...original.database, name: "another-database" } },
    { database: { ...original.database, volume: "another-volume" } }, { state: "unknown" },
    { starting_schema: 28.5 }, { intended_schema: 29.5 }, { starting_schema: 0 }, { intended_schema: 27 },
    { starting_versions: null }, { starting_versions: [1] },
    { starting_versions: original.starting_versions.map(version => version === 2 ? 3 : version) },
    { study_invariants: null }, { backup: { ...original.backup, verified: false } },
    { backup: { ...original.backup, filename: "" } }].map(invalid => ({ ...original, ...invalid }));
  const cases = [
    ...invalidGuards.map(guard => ({ guard, expected: "blocked" })),
    ...[{ phase: "applying_migrations" }, { phase: "failed", failed_phase: "applying_migrations" }]
      .map(operation => ({ guard: null, operation, expected: "blocked" })),
    { guard: validDiagnosticMigrationGuard(fixture), expected: "pending" },
    { guard: original, expected: "clear" },
    { guard: null, operation: { phase: "failed", failed_phase: "preparing_images" }, expected: "clear" },
    { guard: null, expected: "clear" },
  ];
  for (const { guard, operation = null, expected } of cases) for (const retry of [false, true]) {
    const before = JSON.stringify({ guard, operation, target: fixture.target, configuration });
    const assessment = assessMigrationRecovery({ guard, operation, target: fixture.target, configuration, retry });
    assert.equal(assessment.status, expected === "pending" ? retry ? "retry-authorized" : "blocked" : expected);
    assert.equal(JSON.stringify({ guard, operation, target: fixture.target, configuration }), before, "assessment does not mutate inputs");
    const guardPath = join(fixture.stateDirectory, "migration-guard.json");
    const journalPath = join(fixture.stateDirectory, "operation.json");
    if (guard) writeFileSync(guardPath, JSON.stringify(guard)); else rmSync(guardPath, { force: true });
    if (operation) writeFileSync(journalPath, JSON.stringify(operation)); else rmSync(journalPath, { force: true });
    const runtime = createRuntime(fixture.target, { run: async () => ({ stdout: JSON.stringify(configuration) }),
      stateDirectory: fixture.stateDirectory, revision: "a".repeat(40), retry, log: () => {} });
    await runtime.config();
    if (assessment.status === "blocked") assert.throws(() => runtime.checkMigrationRetry(), error => error.message === assessment.message);
    else assert.equal(runtime.checkMigrationRetry(), assessment.status === "retry-authorized");
    assert.equal(existsSync(guardPath) ? readFileSync(guardPath, "utf8") : null, guard ? JSON.stringify(guard) : null);
    assert.equal(existsSync(journalPath) ? readFileSync(journalPath, "utf8") : null, operation ? JSON.stringify(operation) : null);
  }
});

test("CLI migration recovery preflight rereads guard and journal under the lock before maintenance", async t => {
  for (const recoveryState of ["unguarded-interruption", "invalid-verified-guard", "pending-guard"]) {
    const fixture = diagnosticFixture(t, { receipt: true });
    const configuration = readFixtureJson(fixture, "fixture.json").config;
    const runtime = createRuntime(fixture.target, { run: async () => ({ stdout: JSON.stringify(configuration) }),
      stateDirectory: fixture.stateDirectory, revision: "a".repeat(40), log: () => {} });
    await runtime.config();
    assert.equal(runtime.checkMigrationRetry(), false, "initial inspection has no recovery debt");
    const release = acquireTargetLock(fixture.stateDirectory);
    try {
      const operation = { phase: "failed", failed_phase: "applying_migrations", failure: "preserve original failure" };
      writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify(operation));
      if (recoveryState !== "unguarded-interruption") {
        const guard = validDiagnosticMigrationGuard(fixture, recoveryState === "pending-guard" ? "pending" : "verified");
        if (recoveryState === "invalid-verified-guard") guard.target = "another-target";
        writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify(guard));
      }
      const preservedPaths = ["operation.json", "deployment.json", ...(recoveryState !== "unguarded-interruption" ? ["migration-guard.json"] : [])]
        .map(name => join(fixture.stateDirectory, name));
      const before = preservedPaths.map(path => readFileSync(path, "utf8"));
      let maintenanceStarted = false, failureRecorded = false;
      await assert.rejects(executeLifecycle({ recreate: false }, { ...runtime,
        ensureImages: async () => { maintenanceStarted = true; throw new Error("unexpected maintenance"); },
        recordFailure: async () => { failureRecorded = true; },
      }), recoveryState === "unguarded-interruption" ? /without a durable original guard/
        : recoveryState === "invalid-verified-guard" ? /invalid or belongs to another database/ : /tempo migrate --retry/);
      assert.equal(maintenanceStarted, false);
      assert.equal(failureRecorded, false, "rejection cannot replace original failure evidence");
      assert.deepEqual(preservedPaths.map(path => readFileSync(path, "utf8")), before);
      assert.deepEqual(fixture.calls(), []);
    } finally { release(); }
  }
});

test("actual CLI diagnostics explain pending exact-main verification without a deployment receipt", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "in_progress" }] } });
  const result = fixture.command("doctor", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: pending/);
  assert.match(result.stdout, /No previous verified deployment is recorded.*no verified fallback/);
  assert.match(result.stdout, /has not applied the update/);
  assert.match(result.stdout, /https:\/\/github.com\/fixture\/ci/);
  assert.match(result.stdout, /Run: tempo start/);
  assert.match(result.stdout, /wait up to 30 minutes/);
  assert.match(result.stdout, /Local checkout: main.*a{40}/);
  assert.match(result.stdout, /Background completion: unverified/);
});

test("actual CLI diagnostics identify a failed required job without a deployment receipt", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { jobs: ["plan", "frontend / verify", "backend / verify", "build / verify",
    "postgres / verify", "lifecycle / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed",
      conclusion: name === "backend / verify" ? "failure" : "success" })) } });
  const result = fixture.command("status", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: failed.*backend \/ verify.*failure/);
  assert.doesNotMatch(result.stdout, /still pending/);
});

test("actual CLI diagnostics report an eligible candidate without claiming deployment", t => {
  const fixture = diagnosticFixture(t);
  const result = fixture.command("start", "--plan", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: verified/);
  assert.match(result.stdout, /Update eligibility: eligible/);
  assert.match(result.stdout, /Verified deployment: not yet recorded/);
  assert.doesNotMatch(result.stdout, /\(deployed\)/);
});

test("actual CLI diagnostics distinguish local schema debt from running API HTTP 200", t => {
  const fixture = diagnosticFixture(t, { machine: { schema: 28, missingImageRevision: true } });
  const result = fixture.command("doctor", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Pending local migrations: 29/);
  assert.match(result.stdout, /Running application revision: unknown/);
  assert.match(result.stdout, /API health: HTTP 200/);
  assert.match(result.stdout, /running API.*does not prove.*background/);
});

const diagnosticJobs = ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify", "lifecycle / verify",
  "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed", conclusion: "success" }));

test("complete release evidence requires the lifecycle job", () => {
  const lifecycleJob = { name: "lifecycle / verify", status: "completed", conclusion: "success" };
  const completedJobs = [...diagnosticJobs.filter(job => job.name !== lifecycleJob.name), lifecycleJob];
  assert.doesNotThrow(() => qualityEvidence(diagnosticMainRun.head_sha, diagnosticMainRun, completedJobs));
  assert.throws(() => qualityEvidence(diagnosticMainRun.head_sha, diagnosticMainRun,
    completedJobs.filter(job => job.name !== lifecycleJob.name)), /lifecycle/);
  for (const conclusion of ["failure", "cancelled", "skipped", null]) {
    assert.throws(() => qualityEvidence(diagnosticMainRun.head_sha, diagnosticMainRun,
      completedJobs.map(job => job.name === lifecycleJob.name ? { ...job, conclusion } : job)), /lifecycle/);
  }
});

function assessmentClient(runs, jobs = diagnosticJobs) {
  return async path => path.includes("workflows/") ? { workflow_runs: runs } : { jobs };
}

test("CLI verification assessment preserves earlier exact-SHA successes and ignores Pages publication", async () => {
  for (const laterStatus of ["in_progress", "completed"]) {
    const runs = [{ ...diagnosticMainRun, id: 13, status: laterStatus }, diagnosticMainRun];
    const client = async path => path.includes("workflows/") ? { workflow_runs: runs } : { jobs: path.includes("/13/")
      ? diagnosticJobs.map(job => ({ ...job, conclusion: "failure" }))
      : [...diagnosticJobs, { name: "pages-build", status: "completed", conclusion: "failure" }] };
    const assessment = await assessMainVerification(diagnosticMainRun.head_sha, client);
    assert.equal(assessment.status, "verified");
    assert.equal(assessment.evidence.run_id, 12);
  }
});

test("CLI verification assessment rejects unrelated SHA branch event and incomplete required jobs", async () => {
  for (const invalid of [{ head_sha: "b".repeat(40) }, { head_branch: "feature" }, { event: "pull_request" }, { event: "merge_group" }]) {
    const assessment = await assessMainVerification(diagnosticMainRun.head_sha, assessmentClient([{ ...diagnosticMainRun, ...invalid }]));
    assert.equal(assessment.status, "missing");
    assert.equal(assessment.evidence, undefined);
  }
  const missing = await assessMainVerification(diagnosticMainRun.head_sha, assessmentClient([diagnosticMainRun], diagnosticJobs.filter(job => job.name !== "visual / verify")));
  assert.equal(missing.status, "missing"); assert.equal(missing.job, "visual / verify");
  const incomplete = await assessMainVerification(diagnosticMainRun.head_sha, assessmentClient([diagnosticMainRun], diagnosticJobs.map(job => job.name === "quality" ? { ...job, status: "in_progress", conclusion: null } : job)));
  assert.equal(incomplete.status, "pending"); assert.equal(incomplete.job, "quality");
  for (const conclusion of ["failure", "cancelled", "skipped", "timed_out"]) {
    const failed = await assessMainVerification(diagnosticMainRun.head_sha, assessmentClient([diagnosticMainRun], diagnosticJobs.map(job => job.name === "postgres / verify" ? { ...job, conclusion } : job)));
    assert.equal(failed.status, "failed"); assert.equal(failed.conclusion, conclusion);
  }
});

test("CLI verification assessment bounds a hung client and never calls incomplete evidence missing", async () => {
  const controller = new AbortController();
  let requested;
  const started = new Promise(resolveStarted => { requested = resolveStarted; });
  const assessmentPromise = assessMainVerification(diagnosticMainRun.head_sha, () => {
    requested(); return new Promise(() => {});
  }, { signal: controller.signal });
  await started;
  controller.abort(new Error("diagnostic deadline reached"));
  const assessment = await assessmentPromise;
  assert.equal(assessment.status, "unavailable");
  assert.match(assessment.message, /deadline/);
});

test("CLI verification assessment accepts success after unavailable separate-run jobs", async () => {
  const runs = [{ ...diagnosticMainRun, id: 13 }, diagnosticMainRun];
  const client = async path => {
    if (path.includes("workflows/")) return { workflow_runs: runs };
    if (path.includes("/13/")) throw new Error("GitHub HTTP 429");
    return { jobs: diagnosticJobs };
  };
  assert.equal((await assessMainVerification(diagnosticMainRun.head_sha, client)).status, "verified");
});

test("actual CLI diagnostics retain recorded fallback while newer verification is blocked", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = fixture.command("status", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Recorded fallback: b{40}.*images, schema and readiness/);
  assert.match(result.stdout, /Running application revision: consistent.*a{40}/);
  assert.match(result.stdout, /Receipt consistency: differs from running service identities/);
  assert.match(result.stdout, /Verification: pending/);
});

test("actual CLI diagnostics preserve local facts through GitHub access rate-limit timeout and remote-main failure", t => {
  for (const diagnostics of [{ githubStatus: 403 }, { githubStatus: 429 }, { githubStatus: 200 }, { githubError: "request timed out" }, { remoteFailure: true }]) {
    const fixture = diagnosticFixture(t, { diagnostics });
    const result = fixture.command("doctor", "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Local checkout: main/);
    assert.match(result.stdout, /Applied schema versions: 1, 2/);
    assert.match(result.stdout, /Verification: unavailable/);
    assert.match(result.stdout, /not a failed test/);
    assert.match(result.stdout, /API health: HTTP 200/);
  }
});

test("actual CLI diagnostics report mixed partial and unavailable immutable image evidence", t => {
  for (const [machine, expected] of [
    [{ imageInspectionUnavailable: true }, /Running application revision: unknown.*inspection unavailable/],
    [{ imageRevisions: { "sha256:fixture-api": "b".repeat(40) } }, /Running application revision: mixed/],
    [{ running: ["postgres", "api"] }, /Running application revision: partial.*missing services:.*web/],
  ]) {
    const fixture = diagnosticFixture(t, { machine });
    const result = fixture.command("status", "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expected);
    assert.match(result.stdout, /Applied schema versions:/);
  }
});

test("actual CLI diagnostics report migration gaps and database-ahead source independently", t => {
  const fixture = diagnosticFixture(t, { machine: { appliedVersions: [1, 2, 4, 30] } });
  const result = fixture.command("status", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Migration ledger gaps: 3, 5/);
  assert.match(result.stdout, /Database schema 30 is ahead of local source 29/);
});

test("actual CLI diagnostic safety preserves source guards and unavailable local ancestry without fetching", t => {
  for (const [machine, expected] of [
    [{ sourceEdited: true }, /Update eligibility: blocked.*local changes/i],
    [{ branch: "study-work" }, /Update eligibility: blocked.*checkout.*on main/i],
    [{ remote: "https://github.com/unrelated/tempo" }, /Update eligibility: blocked.*unexpected GitHub remote/],
    [{ head: "c".repeat(40), ancestryCode: 1 }, /Update eligibility: blocked.*diverged/],
    [{ head: "c".repeat(40), remoteObjectMissing: true }, /Update eligibility: unknown.*ancestry/],
  ]) {
    const fixture = diagnosticFixture(t, { machine });
    const result = fixture.command("status", "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expected);
    assert(!fixture.calls().some(call => call.args.includes("fetch") || call.args.includes("merge")));
  }
});

test("actual CLI diagnostic safety leaves source state receipts guards services and database unchanged", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "in_progress" }] } });
  writeFileSync(join(fixture.stateDirectory, "operation.json"), JSON.stringify({ phase: "failed", failure: "existing failure evidence" }));
  writeFileSync(join(fixture.stateDirectory, "migration-guard.json"), JSON.stringify({ state: "pending", origin_revision: "b".repeat(40), backup: { filename: "original.dump" } }));
  const preservedPaths = [join(fixture.directory, "machine.json"), fixture.registration,
    ...readdirSync(fixture.stateDirectory).map(name => join(fixture.stateDirectory, name)),
    join(fixture.root, "backend/app/schema_version.py")];
  const before = preservedPaths.map(path => readFileSync(path, "utf8"));
  for (const argumentsList of [["status"], ["doctor"], ["start", "--plan"], ["restart", "--plan"], ["migrate", "--plan"]]) {
    const result = fixture.command(...argumentsList, "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Original migration verification: pending/);
    assert.match(result.stdout, /tempo migrate --retry explicitly/);
  }
  assert.deepEqual(preservedPaths.map(path => readFileSync(path, "utf8")), before);
  assert.deepEqual(readdirSync(fixture.stateDirectory).sort(), ["deployment.json", "migration-guard.json", "operation.json"]);
  for (const request of readFileSync(join(fixture.directory, "requests.jsonl"), "utf8").trim().split("\n").map(JSON.parse)) {
    assert.equal(request.method, "GET");
    assert(request.url.includes("api.github.com") || request.url.endsWith("/api/health"));
  }
  for (const call of fixture.calls()) assert(!["fetch", "merge", "build", "pull", "run", "up", "stop"].some(argument => call.args.includes(argument)));
  for (const call of fixture.calls().filter(call => call.args.includes("psql"))) {
    assert(call.args.some(argument => argument.includes("default_transaction_read_only=on")));
    assert(call.args.some(argument => argument.includes("statement_timeout=1000")));
    assert(call.args.some(argument => argument.includes("lock_timeout=100")));
    assert.equal(call.args.at(-1), "SELECT version FROM tempo_schema_migrations ORDER BY version");
  }
});

test("actual CLI diagnostic safety logs remain available without contacting GitHub", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { githubError: "GitHub unavailable" } });
  const result = fixture.command("logs", "api");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert(fixture.calls().some(call => call.args.includes("logs") && call.args.includes("api")));
  assert(!fixture.calls().some(call => call.command === "git"));
  assert(!existsSync(join(fixture.directory, "requests.jsonl")));
});

test("actual CLI diagnostic safety reports receipt image and configuration drift without changing containers", t => {
  for (const drift of ["Image", "configuration"]) {
    const fixture = diagnosticFixture(t, { receipt: true });
    const machinePath = join(fixture.directory, "machine.json");
    const machine = JSON.parse(readFileSync(machinePath, "utf8"));
    const api = machine.containers.find(container => container.Config.Labels["com.docker.compose.service"] === "api");
    if (drift === "Image") api.Image = "sha256:unrecorded-api";
    else api.Config.Labels["com.docker.compose.config-hash"] = "uncommitted-config";
    writeFileSync(machinePath, JSON.stringify(machine));
    const before = readFileSync(machinePath, "utf8");
    const result = fixture.command("status", "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Receipt consistency: differs from running service identities/);
    assert.equal(readFileSync(machinePath, "utf8"), before);
  }
});

test("actual CLI diagnostic safety no-receipt blocked start explains preserved deployment state", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "in_progress" }] } });
  const result = fixture.command("restart", "--no-open", "--no-wait");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /No previous verified deployment is recorded.*no verified fallback/);
  assert.match(result.stderr, /blocked attempt has not applied the update/);
  assert(!fixture.calls().some(call => ["build", "pull", "up", "stop"].some(argument => call.args.includes(argument))));
  assert(!existsSync(join(fixture.stateDirectory, "deployment.json")));
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
});

test("actual CLI diagnostic safety unavailable schema reads retain other diagnostics and zero exit", t => {
  const fixture = diagnosticFixture(t, { machine: { ledgerReadUnavailable: true } });
  const result = fixture.command("doctor", "--verbose");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Database schema could not be read/);
  assert.match(result.stdout, /Verification: verified/);
  assert.match(result.stdout, /API health: HTTP 200/);
  assert.doesNotMatch(result.stdout, /Applied schema versions: 1/);
});

test("actual CLI diagnostics retain partial immutable inspection evidence and separate receipt identity", t => {
  for (const [machine, expectedRevision, expectedReceipt] of [
    [{ unavailableImageIds: ["sha256:fixture-web"] }, /Running application revision: unknown.*available revision evidence \(incomplete\): a{40}/, /Receipt consistency: matches immutable images and configuration of inspected running services/],
    [{ unavailableImageIds: ["sha256:fixture-web"], imageRevisions: { "sha256:fixture-api": "b".repeat(40) } }, /Running application revision: mixed.*a{40}.*b{40}|Running application revision: mixed.*b{40}.*a{40}/, /Receipt consistency: differs from running service identities/],
    [{ imageInspectionUnavailable: true }, /Running application revision: unknown/, /Receipt consistency: matches immutable images and configuration of inspected running services/],
  ]) {
    const fixture = diagnosticFixture(t, { receipt: true, machine });
    const receiptPath = join(fixture.stateDirectory, "deployment.json");
    const receipt = JSON.parse(readFileSync(receiptPath, "utf8"));
    receipt.revision = "a".repeat(40); receipt.evidence.commit = receipt.revision;
    writeFileSync(receiptPath, JSON.stringify(receipt));
    const before = readFileSync(join(fixture.directory, "machine.json"), "utf8");
    const result = fixture.command("status", "--verbose");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expectedRevision);
    assert.match(result.stdout, expectedReceipt);
    assert.match(result.stdout, /Applied schema versions:/);
    assert.equal(readFileSync(join(fixture.directory, "machine.json"), "utf8"), before);
    const batchCalls = fixture.calls().filter(call => call.args.includes("image") && call.args.includes("inspect"));
    assert.equal(batchCalls.length, 1, "Unavailable metadata must not cause per-image deadline multiplication");
  }
});

async function guidance() { return import("../../scripts/tempo-guidance.mjs"); }
function verificationClock() {
  let elapsed = 0;
  return { now: () => elapsed, wait: async milliseconds => { elapsed += milliseconds; }, elapsed: () => elapsed };
}
function pendingCandidate(revision = "a".repeat(40), status = "pending") {
  return { revision, verification: { status, message: status, run: { html_url: "https://github.com/fixture/ci" } } };
}

test("CLI pending verification waits until exact main succeeds with bounded progress", async () => {
  const { waitForVerification } = await guidance();
  const clock = verificationClock(), progress = [];
  let attempts = 0;
  const result = await waitForVerification({ ...clock, log: message => progress.push(message),
    assess: async () => pendingCandidate("a".repeat(40), ++attempts === 3 ? "verified" : "pending") });
  assert.equal(result.verification.status, "verified");
  assert.equal(clock.elapsed(), 120_000);
  assert.equal(attempts, 3);
  assert(progress[0].includes("30 minutes") && progress[0].includes("Study may pause"));
});

test("CLI pending verification deadline never resets when main advances", async () => {
  const { waitForVerification } = await guidance();
  const clock = verificationClock(); let attempts = 0;
  const result = await waitForVerification({ ...clock, log: () => {},
    assess: async () => pendingCandidate((++attempts % 2 ? "a" : "b").repeat(40)) });
  assert.equal(result.timedOut, true);
  assert.equal(clock.elapsed(), 1_800_000);
  assert.equal(attempts, 31);
});

for (const status of ["failed", "missing", "unavailable"]) test(`CLI pending verification stops immediately on ${status} evidence`, async () => {
  const { waitForVerification } = await guidance();
  const clock = verificationClock(); let attempts = 0;
  const result = await waitForVerification({ ...clock, log: () => {}, assess: async () => pendingCandidate("a".repeat(40), ++attempts === 1 ? "pending" : status) });
  assert.equal(result.verification.status, status);
  assert.equal(clock.elapsed(), 60_000);
});

test("CLI pending verification no-wait and read-only assessment never delay", async () => {
  const { waitForVerification } = await guidance();
  let attempts = 0;
  const result = await waitForVerification({ noWait: true, assess: async () => { attempts++; return pendingCandidate(); }, wait: () => assert.fail("no-wait cannot sleep") });
  assert.equal(result.verification.status, "pending");
  assert.equal(attempts, 1);
});

test("CLI pending verification Ctrl-C aborts sleep without deploying", async () => {
  const { waitForVerification } = await guidance();
  const cancellation = new AbortController(); let checks = 0;
  await assert.rejects(waitForVerification({ signal: cancellation.signal, log: () => {}, assess: async () => { checks++; return pendingCandidate(); },
    wait: async () => { cancellation.abort(); throw new Error("sleep interrupted"); } }), error => error.code === "cancelled" && error.exitCode === 130);
  assert.equal(checks, 1);
});

test("CLI pending verification Ctrl-C aborts an in-flight request", async () => {
  const { waitForVerification } = await guidance();
  const cancellation = new AbortController();
  await assert.rejects(waitForVerification({ signal: cancellation.signal,
    assess: async () => { cancellation.abort(); throw new Error("fetch aborted"); } }), error => error.code === "cancelled" && error.exitCode === 130);
});

for (const stateFile of ["operation.json", "deployment.json", "migration-guard.json", "config.json"]) test(`CLI waiting installation fence cancels on concurrent ${stateFile} changes`, async t => {
  const { installationFingerprint, assertInstallationUnchanged, waitForVerification } = await guidance();
  const path = directory(t), config = join(path, "config.json");
  writeFileSync(config, "{}");
  const before = installationFingerprint(path, config);
  const changed = join(path, stateFile);
  const clock = verificationClock();
  await assert.rejects(waitForVerification({ ...clock, log: () => {}, assess: async () => pendingCandidate(),
    checkUnchanged: () => assertInstallationUnchanged(before, path, config), wait: async milliseconds => {
      await clock.wait(milliseconds); writeFileSync(changed, JSON.stringify({ phase: "stopped", id: "concurrent-command" }));
    } }), error => error.code === "installation_changed");
  assert.equal(clock.elapsed(), 60_000);
  assert.equal(readFileSync(changed, "utf8"), JSON.stringify({ phase: "stopped", id: "concurrent-command" }));
});

test("CLI waiting installation fence catches a second deployment under the reacquired lock", async t => {
  const { installationFingerprint, assertInstallationUnchanged } = await guidance();
  const path = directory(t), config = join(path, "config.json"); writeFileSync(config, "{}");
  const first = installationFingerprint(path, config), second = installationFingerprint(path, config);
  const release = acquireTargetLock(path);
  assertInstallationUnchanged(first, path, config);
  atomicJson(join(path, "operation.json"), { id: "first-deployment", phase: "ready" });
  release();
  const releaseSecond = acquireTargetLock(path);
  try { assert.throws(() => assertInstallationUnchanged(second, path, config), error => error.code === "installation_changed"); }
  finally { releaseSecond(); }
});

test("actual CLI diagnostics explain one primary next action when source and verification are blocked", t => {
  const fixture = diagnosticFixture(t, { machine: { sourceEdited: true }, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Local changes are preserved/);
  assert.match(result.stdout, /personal-work/);
  assert.match(result.stdout, /isolated checkout/);
  assert(!result.stdout.includes("Tempo will wait"));
  assert.equal(result.stdout.match(/Next:/g)?.length, 1);
});

test("actual CLI diagnostics explain safe recovery instead of ordinary start for an unfinished migration", t => {
  const fixture = diagnosticFixture(t);
  atomicJson(join(fixture.stateDirectory, "migration-guard.json"), validDiagnosticMigrationGuard(fixture));
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /original.dump/);
  assert.match(result.stdout, /repair.*before tempo migrate --retry/i);
  assert(!result.stdout.includes("Run: tempo start"));
});

test("CLI failure evidence selects Redis logs and redacts replies without unrelated API logs", async t => {
  const secret = "canary-private-password";
  const fixture = redisRuntimeFixture(t, [{ code: 1, stdout: "", stderr: `NOAUTH ${secret}` }]);
  await fixture.runtime.config(); await fixture.runtime.ensureImages();
  let failure;
  try { await fixture.runtime.ensureDatabase(); } catch (error) { failure = error; }
  assert.match(failure.message, /Redis is not ready/);
  await fixture.runtime.recordFailure(failure);
  const operation = JSON.parse(readFileSync(join(fixture.fixture.stateDirectory, "operation.json"), "utf8"));
  assert.equal(operation.failed_phase, "checking_redis");
  const logs = fixture.calls.find(args => args.includes("logs"));
  assert(logs.includes("redis") && !logs.includes("api"));
  const evidence = readFileSync(operation.failure_log, "utf8");
  assert(evidence.includes("checking_redis") && evidence.includes("[redacted]"));
  assert(!evidence.includes(secret));
});

function commandWithControlledWait(fixture, onWait, args = ["start", "--no-open"], expireOnFirstWait = false) {
  const driver = join(fixture.directory, "controlled-start.mjs");
  writeFileSync(driver, `
import { main } from ${JSON.stringify(new URL("../../scripts/tempo-cli.mjs", import.meta.url).href)};
import { spawnSync as fsSpawnSync } from "node:child_process";
const cliEntry = ${JSON.stringify(cliEntry)};
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
let elapsed = 0, ticks = 0;
const fixtureDirectory = process.env.TEMPO_CLI_FIXTURE_DIRECTORY;
try {
  process.exitCode = await main(process.argv.slice(2), console.log, { verificationWaitOptions: {
    now: () => elapsed,
    wait: async milliseconds => {
      if (existsSync(join(fixtureDirectory, "state", ${JSON.stringify(targetKey(fixture.target))}, "maintenance.lock"))) throw new Error("waiting held maintenance lock");
      elapsed += ${expireOnFirstWait ? "1_800_000" : "milliseconds"}; ticks++;
      await (${onWait.toString()})(fixtureDirectory, ticks);
    }
  } });
} catch (error) { console.error(error.message); if (error.action) console.error(error.action); process.exitCode = error.exitCode ?? 1; }
`);
  return fsSpawnSync(process.execPath, ["--import", fixture.hook, driver, ...args, "--config", fixture.registration],
    { encoding: "utf8", env: fixture.environment, timeout: 30_000 });
}

test("CLI automatic start waits without locking then deploys only after verified evidence", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = commandWithControlledWait(fixture, path => {
    const file = join(path, "fixture.json"), data = JSON.parse(readFileSync(file, "utf8"));
    data.diagnostics.runs[0].status = "completed"; writeFileSync(file, JSON.stringify(data));
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /wait up to 30 minutes/);
  assert.match(result.stdout, /deployment verified/);
  assert.equal(readFixtureJson(fixture, "deployment.json", true).evidence.commit, "a".repeat(40));
});

test("CLI automatic start timeout preserves no-receipt installation and offers one retry step", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const machine = readFileSync(join(fixture.directory, "machine.json"), "utf8");
  const result = commandWithControlledWait(fixture, () => {}, ["start", "--no-open"], true);
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout + result.stderr, /still pending after the 30-minute wait/);
  assert.match(result.stdout + result.stderr, /run tempo start later/);
  assert(!fixture.calls().some(call => ["fetch", "merge", "up", "stop", "build"].some(argument => call.args.includes(argument))));
  assert.equal(readFileSync(join(fixture.directory, "machine.json"), "utf8"), machine);
  assert.deepEqual(readdirSync(fixture.stateDirectory), []);
});

test("CLI automatic start timeout validates fallback and reports update deferred", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] }, machine: { schema: 29 } });
  const saved = readFixtureJson(fixture, "deployment.json", true); saved.schema = 29;
  atomicJson(join(fixture.stateDirectory, "deployment.json"), saved);
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  const result = commandWithControlledWait(fixture, () => {}, ["start", "--no-open"], true);
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /previous verified version ready, update deferred/);
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assert(!fixture.calls().some(call => call.args.includes("build") || call.args.includes("merge")));
});

test("CLI automatic migrate timeout never substitutes a recorded fallback", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = commandWithControlledWait(fixture, () => {}, ["migrate"], true);
  assert.equal(result.status, 1);
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop") || call.args.includes("build")));
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
});

test("CLI automatic start cancels after concurrent stop without undoing stopped services", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = commandWithControlledWait(fixture, path => {
    // Invoke the actual stop command while the other actual command is waiting.
    const config = join(path, "config.json");
    const result = fsSpawnSync(process.execPath, [cliEntry, "stop", "--config", config],
      { encoding: "utf8", env: process.env, timeout: 10_000 });
    if (result.status !== 0) throw new Error(result.stdout + result.stderr);
  });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /will not restart it/);
  assert.equal(readFixtureJson(fixture, "operation.json", true).phase, "stopped");
  assert.deepEqual(readFixtureJson(fixture, "machine.json").running, []);
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build")));
});

test("CLI automatic start cancels on source drift without rebuilding or falling back", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = commandWithControlledWait(fixture, path => {
    const file = join(path, "machine.json"), machine = JSON.parse(readFileSync(file, "utf8"));
    machine.head = "c".repeat(40); writeFileSync(file, JSON.stringify(machine));
  });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /Local source changed/);
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build") || call.args.includes("merge")));
});

test("CLI automatic start cancels when source inspection becomes unavailable after waiting begins", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  const result = commandWithControlledWait(fixture, path => {
    const file = join(path, "machine.json"), machine = JSON.parse(readFileSync(file, "utf8"));
    machine.gitProbeFailure = { probe: "branch" }; writeFileSync(file, JSON.stringify(machine));
  });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout, /wait up to 30 minutes/);
  assert.match(result.stderr, /source.*(fence|inspect).*cancelled/i);
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
  assert(!fixture.calls().some(call => ["up", "stop", "build", "fetch", "merge"].some(argument => call.args.includes(argument))));
});

test("CLI automatic start cancels on programming errors during source inspection without falling back", t => {
  const fixture = diagnosticFixture(t, { receipt: true, diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const receipt = readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8");
  const result = commandWithControlledWait(fixture, async () => {
    const childProcess = (await import("node:child_process")).default;
    const launchProcess = childProcess.spawn;
    let sourceExceptionPending = true;
    childProcess.spawn = (command, ...argumentsList) => {
      if (command === "git" && sourceExceptionPending) {
        sourceExceptionPending = false;
        throw new TypeError("injected internal source-inspection defect");
      }
      return launchProcess(command, ...argumentsList);
    };
    (await import("node:module")).syncBuiltinESMExports();
  });
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout, /wait up to 30 minutes/);
  assert.match(result.stderr, /injected internal source-inspection defect/);
  assert.doesNotMatch(result.stdout + result.stderr, /Attempting previously verified|source state could not be inspected/);
  assert.equal(readFileSync(join(fixture.stateDirectory, "deployment.json"), "utf8"), receipt);
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
  assert(!fixture.calls().some(call => ["up", "stop", "build", "fetch", "merge"].some(argument => call.args.includes(argument))));
});

test("CLI automatic start cancels on actual SIGINT without a deployment record", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "queued" }] } });
  const result = commandWithControlledWait(fixture, () => { process.kill(process.pid, "SIGINT"); });
  assert.equal(result.status, 130, result.stdout + result.stderr);
  assert.match(result.stderr, /Waiting cancelled/);
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
  assert(!fixture.calls().some(call => call.args.includes("stop") || call.args.includes("up") || call.args.includes("fetch")));
});

function installRelaunchFixture(fixture, stopOnEntry) {
  editFixtureJson(fixture, "machine.json", value => { value.head = "c".repeat(40); });
  mkdirSync(join(fixture.root, "scripts"));
  writeFileSync(join(fixture.root, "scripts/tempo-cli.mjs"), `
import ${JSON.stringify(fixture.hook)};
import { main } from ${JSON.stringify(new URL("../../scripts/tempo-cli.mjs", import.meta.url).href)};
import { writeFileSync } from "node:fs";
const continued = JSON.parse(process.env.TEMPO_CLI_CONTINUATION);
writeFileSync(${JSON.stringify(join(fixture.directory, "observed-continuation.json"))}, JSON.stringify(continued));
${stopOnEntry ? `writeFileSync(${JSON.stringify(join(fixture.stateDirectory, "operation.json"))}, JSON.stringify({ id: "concurrent-stop", phase: "stopped" }));` : ""}
try { process.exitCode = await main(process.argv.slice(2)); }
catch (error) { console.error(error.message); if (error.action) console.error(error.action); process.exitCode = error.exitCode ?? 1; }
`);
}

test("CLI automatic start relaunch preserves the original fence across a concurrent stop", t => {
  const fixture = diagnosticFixture(t);
  installRelaunchFixture(fixture, true);
  const result = fixture.command("start", "--no-open");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout + result.stderr, /will not restart it/);
  assert.equal(readFixtureJson(fixture, "operation.json", true).id, "concurrent-stop");
  const continued = readFixtureJson(fixture, "observed-continuation.json");
  assert.equal(continued.source.head, "a".repeat(40));
  assert.deepEqual(Object.keys(continued.source).sort(), ["branch", "changes", "head", "origin"]);
  assert.equal(continued.installationState[join(fixture.stateDirectory, "operation.json")], null);
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build")));
});

test("CLI automatic start relaunch carries the original deadline instead of another thirty minutes", t => {
  const fixture = diagnosticFixture(t);
  installRelaunchFixture(fixture, false);
  const wrapper = join(fixture.root, "scripts/tempo-cli.mjs");
  const text = readFileSync(wrapper, "utf8");
  writeFileSync(wrapper, text.replace("try { process.exitCode", `
import { readFileSync } from "node:fs";
const fixturePath = ${JSON.stringify(join(fixture.directory, "fixture.json"))};
const data = JSON.parse(readFileSync(fixturePath, "utf8"));
data.diagnostics.runs = [{ id: 12, head_sha: data.revision, head_branch: "main", event: "push", status: "queued", html_url: "https://github.com/fixture/ci" }];
writeFileSync(fixturePath, JSON.stringify(data));
try { process.exitCode`).replace("await main(process.argv.slice(2))", "await main(process.argv.slice(2), console.log, { verificationWaitOptions: { now: () => continued.deadline } })"));
  const result = commandWithControlledWait(fixture, () => {});
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stdout + result.stderr, /30-minute wait/);
  assert.equal(readFixtureJson(fixture, "observed-continuation.json").deadline, 1_800_000);
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build")));
});

test("actual CLI diagnostics explain current terminal Redis errors with one repair action and redaction", t => {
  const secret = "canary-private-password";
  const fixture = diagnosticFixture(t, { machine: { redisReply: `NOAUTH ${secret}` } });
  const before = readFileSync(join(fixture.directory, "machine.json"), "utf8");
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Redis rejected its readiness check: NOAUTH \[redacted\]/);
  assert.match(result.stdout, /tempo logs redis/);
  assert(!result.stdout.includes("Run: tempo start"));
  assert(!result.stdout.includes(secret));
  assert.equal(result.stdout.match(/Next:/g)?.length, 1);
  assert.equal(readFileSync(join(fixture.directory, "machine.json"), "utf8"), before);
});

test("actual CLI diagnostic safety concise and verbose modes preserve source receipts guards and services", t => {
  const fixture = diagnosticFixture(t, { receipt: true });
  const snapshots = () => [fixture.registration, join(fixture.directory, "machine.json"),
    ...readdirSync(fixture.stateDirectory).map(name => join(fixture.stateDirectory, name))].map(path => readFileSync(path, "utf8"));
  const before = snapshots();
  for (const args of [["doctor"], ["doctor", "--verbose"], ["status"], ["start", "--plan"]]) {
    const result = fixture.command(...args);
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.equal(result.stdout.match(/Run:/g)?.length, 1);
    assert.deepEqual(snapshots(), before);
  }
  assert(!fixture.calls().some(call => ["fetch", "merge", "reset", "checkout", "build", "pull", "run", "up", "stop"].some(argument => call.args.includes(argument))));
});

test("actual CLI diagnostics explain active maintenance before treating its migration guard as a failure", t => {
  const fixture = diagnosticFixture(t);
  atomicJson(join(fixture.stateDirectory, "migration-guard.json"), validDiagnosticMigrationGuard(fixture));
  const release = acquireTargetLock(fixture.stateDirectory);
  try {
    const result = fixture.command("doctor");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Another Tempo command is performing maintenance/);
    assert.match(result.stdout, /let that command finish/);
    assert(!result.stdout.includes("tempo migrate --retry"));
    const start = fixture.command("start", "--no-open");
    assert.equal(start.status, 1);
    assert.match(start.stderr, /Another Tempo command/);
    assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("fetch")));
  } finally { release(); }
});

test("CLI automatic start waits for advancing main instead of deploying an older verified revision", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { advanceAfterJobs: true } });
  installRelaunchFixture(fixture, false);
  const result = commandWithControlledWait(fixture, path => {
    const file = join(path, "fixture.json"), data = JSON.parse(readFileSync(file, "utf8"));
    data.diagnostics.runs[0].status = "completed"; writeFileSync(file, JSON.stringify(data));
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Waiting for bbbbbbbbbbbb/);
  const receipt = readFixtureJson(fixture, "deployment.json", true);
  assert.equal(receipt.revision, "b".repeat(40));
  assert.equal(receipt.evidence.commit, receipt.revision);
  assert(fixture.calls().filter(call => call.args.includes("merge")).every(call => call.args.at(-1) === receipt.revision));
});

test("CLI automatic start cancels a concurrent stop during initial target inspection", t => {
  const fixture = diagnosticFixture(t, { machine: { stopDuringInspection: true } });
  const result = fixture.command("start", "--no-open");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /will not restart it/);
  assert.equal(readFixtureJson(fixture, "operation.json", true).id, "stop-during-inspection");
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build") || call.args.includes("fetch")));
});

test("actual CLI blocked update reports specific GitHub causes without a fallback or leaked credentials", t => {
  for (const [diagnostics, expected] of [
    [{ githubStatus: 401 }, /authentication or access.*HTTP 401/i],
    [{ githubStatus: 429 }, /rate limit.*HTTP 429/i],
    [{ githubError: "request timed out Bearer canary-private-password" }, /request timed out Bearer \[redacted\]/],
  ]) {
    const fixture = diagnosticFixture(t, { diagnostics });
    const result = fixture.command("start", "--no-wait", "--no-open");
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stderr, expected);
    assert.match(result.stderr, /not a failed test/);
    assert(!result.stderr.includes("canary-private-password"));
    assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("build")));
  }
});

test("actual CLI diagnostics preserve actionable GitHub rate limit and access causes in default output", t => {
  for (const [diagnostics, expected] of [
    [{ githubStatus: 403 }, /authentication or access.*HTTP 403/i],
    [{ githubStatus: 429 }, /rate limit.*HTTP 429/i],
    [{ githubStatus: 403, githubRateLimit: true }, /rate limit.*HTTP 403/i],
  ]) {
    const fixture = diagnosticFixture(t, { diagnostics });
    const result = fixture.command("doctor");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expected);
    assert.equal(result.stdout.match(/Next:/g)?.length, 1);
    assert(!result.stdout.includes("Verification:"));
  }
});

test("actual CLI diagnostics identify the missing release job in default output", t => {
  for (const [diagnostics, expected, link] of [
    [{ jobs: diagnosticJobs.filter(job => job.name !== "backend / verify") }, /evidence is missing.*backend \/ verify/, /https:\/\/github.com\/fixture\/ci/],
    [{ runs: [] }, /evidence is missing for the current main revision/, /https:\/\/github.com\/aaweaver-actuary\/tempo\/actions\/workflows\/pages.yml/],
  ]) {
    const fixture = diagnosticFixture(t, { diagnostics });
    const result = fixture.command("doctor");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expected);
    assert.match(result.stdout, link);
    assert.match(result.stdout, /complete or restore the required release workflow/);
  }
});

test("actual CLI diagnostics explain the named branch requiring preservation", t => {
  const fixture = diagnosticFixture(t, { machine: { branch: "study-edits" } });
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /branch.*study-edits/);
  assert.match(result.stdout, /preserve your work/);
});

test("actual CLI diagnostics keep complete changed-file evidence beyond the concise preview", t => {
  const fixture = diagnosticFixture(t, { machine: { sourceChanges: " M first-work\n M second-work\n M third-work\n?? fourth-work" } });
  const concise = fixture.command("doctor");
  assert.equal(concise.status, 0, concise.stdout + concise.stderr);
  assert.match(concise.stdout, /first-work.*second-work.*third-work/);
  assert(!concise.stdout.includes("fourth-work"));
  assert.match(concise.stdout, /more files listed in tempo doctor --verbose/);
  const detailed = fixture.command("doctor", "--verbose");
  assert.equal(detailed.status, 0, detailed.stdout + detailed.stderr);
  assert.match(detailed.stdout, /Local changes:[\s\S]*fourth-work/);
});

test("actual CLI read-only container inspection refreshes a disappeared transient container once", t => {
  const fixture = diagnosticFixture(t, { machine: { transientInventoryContainer: true } });
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Run: tempo start/);
  assert.equal(fixture.calls().filter(call => call.args.includes("ps") && !call.args.includes("compose")).length, 2);
  assert(!fixture.calls().some(call => ["up", "stop", "build", "fetch"].some(argument => call.args.includes(argument))));
  assert.deepEqual(readdirSync(fixture.stateDirectory), []);
});

test("actual CLI read-only container inspection refuses an unsafe survivor after a transient disappears", t => {
  const fixture = diagnosticFixture(t, { machine: { transientInventoryContainer: true } });
  editFixtureJson(fixture, "machine.json", machine => machine.containers.push({ Id: "unsafe-survivor", Name: "foreign-writer",
    Config: { Labels: { "com.docker.compose.project": "another-project" } },
    Mounts: [{ Type: "volume", Name: "tempo-postgres-data" }] }));
  const result = fixture.command("doctor");
  assert.equal(result.status, 1, result.stdout + result.stderr);
  assert.match(result.stderr, /foreign-writer.*uses Tempo's volume or port/);
  assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop")));
});

test("actual CLI read-only container inspection never hides access malformed or repeated-disappearance failures", t => {
  for (const [machine, expected, attempts] of [
    [{ inventoryInspectionError: "permission denied" }, /permission denied/, 1],
    [{ inventoryMalformed: true }, /container metadata is incomplete or invalid/, 1],
    [{ transientInventoryContainer: true, repeatTransientRemoval: true }, /no such object: deaddeaddead/, 2],
  ]) {
    const fixture = diagnosticFixture(t, { machine });
    const result = fixture.command("doctor");
    assert.equal(result.status, 1, result.stdout + result.stderr);
    assert.match(result.stderr, expected);
    assert.equal(fixture.calls().filter(call => call.args.includes("ps") && !call.args.includes("compose")).length, attempts);
    assert(!fixture.calls().some(call => call.args.includes("up") || call.args.includes("stop")));
    assert.deepEqual(readdirSync(fixture.stateDirectory), []);
  }
});

test("CLI target safety errors name conflicting ports and service mounts without circular doctor advice", t => {
  const fixture = diagnosticFixture(t);
  const original = readFixtureJson(fixture, "fixture.json").config;
  const portConflict = structuredClone(original);
  portConflict.services.web.ports = [{ target: 80, published: "15999", host_ip: "127.0.0.1", protocol: "tcp" }];
  assert.throws(() => validateTarget(portConflict, fixture.target), error => {
    assert.match(error.message, /Compose ports differ.*15999/);
    assert(!error.message.includes("tempo doctor"));
    return true;
  });
  const mountConflict = structuredClone(original);
  mountConflict.services.redis.volumes[0].target = "/wrong";
  assert.throws(() => validateTarget(mountConflict, fixture.target), error => {
    assert.match(error.message, /mount ownership differs for redis/);
    assert.match(error.message, /restore.*mount/i);
    assert(!error.message.includes("tempo doctor"));
    return true;
  });
});

test("CLI verification failure guidance distinguishes cancelled and timed-out jobs from software failures", async () => {
  const { verificationProblem } = await guidance();
  for (const [conclusion, expected] of [["cancelled", /was cancelled/], ["timed_out", /timed out/], ["skipped", /was skipped/]]) {
    const problem = verificationProblem({ status: "failed", job: "postgres / verify", conclusion,
      run: { html_url: "https://github.com/fixture/ci" } });
    assert.match(problem.message, expected);
    assert.match(problem.message, /postgres \/ verify/);
    assert(!problem.message.includes("Software repair is required"));
    assert.match(problem.action, /release workflow.*https:\/\/github.com\/fixture\/ci/);
  }
  const failed = verificationProblem({ status: "failed", job: "postgres / verify", conclusion: "failure" });
  assert.match(failed.message, /software or release workflow/);
  assert(!failed.message.includes("Software repair is required"));
});
