import assert from "node:assert/strict";
import { test } from "node:test";
import fs, { existsSync, mkdtempSync, mkdirSync, readdirSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { schemaVersionFromSource, validateTarget, validateContainers, deploymentCanStart,
  acquireTargetLock, assessMainVerification, qualityEvidence, atomicJson, selectCandidate, productVolumes, targetKey } from "../../scripts/tempo-deployment.mjs";
import { configurationFingerprint, createRuntime, executeLifecycle } from "../../scripts/tempo-runtime.mjs";
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

function editFixtureJson(fixture, name, alter) {
  const value = readFixtureJson(fixture, name); alter(value);
  writeFileSync(join(fixture.directory, name), JSON.stringify(value));
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

function imageRuntimeFixture(t, reference, actualMajor, fallback = false, responseForCommand = () => undefined) {
  const fixture = commandFixture(t, "upgrade");
  const config = readFixtureJson(fixture, "fixture.json").config;
  config.services.postgres.image = reference;
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(config));
  const record = readFixtureJson(fixture, "deployment.json", true);
  const calls = [];
  const run = async (_command, args) => {
    calls.push(args);
    const response = responseForCommand(args);
    if (response !== undefined) return { stdout: response, stderr: "", code: 0 };
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
    preparedImages: { revision: record.revision, images: record.images, configFingerprint: configurationFingerprint(config) }, log: () => {} });
  return { runtime, calls, fixture };
}

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
    assert(result.stdout.includes("update remains blocked"));
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

test("actual CLI concurrent source changes block fast-forward and retain verified fallback", t => {
  for (const mode of ["race-dirty", "race-head"]) {
    const fixture = commandFixture(t, mode);
    const result = fixture.command("start", "--no-open");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert(result.stdout.includes("source changed") && result.stdout.includes("update remains blocked"));
    assert(!fixture.calls().some(call => call.args.includes("merge") || call.args.includes("build")));
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
    return { jobs: ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed", conclusion: failedRuns.includes(id) && name === "quality" ? "failure" : "success" })).filter(job => !incompleteRuns.includes(id) || job.name !== "browser / verify") };
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

test("actual CLI diagnostics explain pending exact-main verification without a deployment receipt", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "in_progress" }] } });
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: pending/);
  assert.match(result.stdout, /No previous verified deployment is recorded.*no verified fallback/);
  assert.match(result.stdout, /has not applied the update/);
  assert.match(result.stdout, /Inspect.*https:\/\/github.com\/fixture\/ci/);
  assert.match(result.stdout, /tempo start.*once eligible/);
  assert.match(result.stdout, /Local checkout: main.*a{40}/);
  assert.match(result.stdout, /Background completion: unverified/);
});

test("actual CLI diagnostics identify a failed required job without a deployment receipt", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { jobs: ["plan", "frontend / verify", "backend / verify", "build / verify",
    "postgres / verify", "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed",
      conclusion: name === "backend / verify" ? "failure" : "success" })) } });
  const result = fixture.command("status");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: failed.*backend \/ verify.*failure/);
  assert.doesNotMatch(result.stdout, /still pending/);
});

test("actual CLI diagnostics report an eligible candidate without claiming deployment", t => {
  const fixture = diagnosticFixture(t);
  const result = fixture.command("start", "--plan");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Verification: verified/);
  assert.match(result.stdout, /Update eligibility: eligible/);
  assert.match(result.stdout, /Verified deployment: not yet recorded/);
  assert.doesNotMatch(result.stdout, /\(deployed\)/);
});

test("actual CLI diagnostics distinguish local schema debt from running API HTTP 200", t => {
  const fixture = diagnosticFixture(t, { machine: { schema: 28, missingImageRevision: true } });
  const result = fixture.command("doctor");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Pending local migrations: 29/);
  assert.match(result.stdout, /Running application revision: unknown/);
  assert.match(result.stdout, /API health: HTTP 200/);
  assert.match(result.stdout, /running API.*does not prove.*background/);
});

const diagnosticJobs = ["plan", "frontend / verify", "backend / verify", "build / verify", "postgres / verify",
  "browser / verify", "visual / verify", "quality"].map(name => ({ name, status: "completed", conclusion: "success" }));

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
  const result = fixture.command("status");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Recorded fallback: b{40}.*images, schema and readiness/);
  assert.match(result.stdout, /Running application revision: consistent.*a{40}/);
  assert.match(result.stdout, /Receipt consistency: differs from running service identities/);
  assert.match(result.stdout, /Verification: pending/);
});

test("actual CLI diagnostics preserve local facts through GitHub access rate-limit timeout and remote-main failure", t => {
  for (const diagnostics of [{ githubStatus: 403 }, { githubStatus: 429 }, { githubStatus: 200 }, { githubError: "request timed out" }, { remoteFailure: true }]) {
    const fixture = diagnosticFixture(t, { diagnostics });
    const result = fixture.command("doctor");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Local checkout: main/);
    assert.match(result.stdout, /Applied schema versions: 1, 2/);
    assert.match(result.stdout, /Verification: unavailable/);
    assert.match(result.stdout, /Unavailable evidence is not a failed test/);
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
    const result = fixture.command("status");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expected);
    assert.match(result.stdout, /Applied schema versions:/);
  }
});

test("actual CLI diagnostics report migration gaps and database-ahead source independently", t => {
  const fixture = diagnosticFixture(t, { machine: { appliedVersions: [1, 2, 4, 30] } });
  const result = fixture.command("status");
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /Migration ledger gaps: 3, 5/);
  assert.match(result.stdout, /Database schema 30 is ahead of local source 29/);
});

test("actual CLI diagnostic safety preserves source guards and unavailable local ancestry without fetching", t => {
  for (const [machine, expected] of [
    [{ sourceEdited: true }, /Update eligibility: blocked.*local changes/],
    [{ branch: "study-work" }, /Update eligibility: blocked.*checkout must be on main/],
    [{ remote: "https://github.com/unrelated/tempo" }, /Update eligibility: blocked.*unexpected GitHub remote/],
    [{ head: "c".repeat(40), ancestryCode: 1 }, /Update eligibility: blocked.*diverged/],
    [{ head: "c".repeat(40), remoteObjectMissing: true }, /Update eligibility: unknown.*ancestry/],
  ]) {
    const fixture = diagnosticFixture(t, { machine });
    const result = fixture.command("status");
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
    const result = fixture.command(...argumentsList);
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
    const result = fixture.command("status");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, /Receipt consistency: differs from running service identities/);
    assert.equal(readFileSync(machinePath, "utf8"), before);
  }
});

test("actual CLI diagnostic safety no-receipt blocked start explains preserved deployment state", t => {
  const fixture = diagnosticFixture(t, { diagnostics: { runs: [{ ...diagnosticMainRun, status: "in_progress" }] } });
  const result = fixture.command("restart", "--no-open");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /No previous verified deployment is recorded.*no verified fallback/);
  assert.match(result.stderr, /blocked attempt has not applied the update/);
  assert(!fixture.calls().some(call => ["build", "pull", "up", "stop"].some(argument => call.args.includes(argument))));
  assert(!existsSync(join(fixture.stateDirectory, "deployment.json")));
  assert(!existsSync(join(fixture.stateDirectory, "operation.json")));
});

test("actual CLI diagnostic safety unavailable schema reads retain other diagnostics and zero exit", t => {
  const fixture = diagnosticFixture(t, { machine: { ledgerReadUnavailable: true } });
  const result = fixture.command("doctor");
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
    const result = fixture.command("status");
    assert.equal(result.status, 0, result.stdout + result.stderr);
    assert.match(result.stdout, expectedRevision);
    assert.match(result.stdout, expectedReceipt);
    assert.match(result.stdout, /Applied schema versions:/);
    assert.equal(readFileSync(join(fixture.directory, "machine.json"), "utf8"), before);
    const batchCalls = fixture.calls().filter(call => call.args.includes("image") && call.args.includes("inspect"));
    assert.equal(batchCalls.length, 1, "Unavailable metadata must not cause per-image deadline multiplication");
  }
});
