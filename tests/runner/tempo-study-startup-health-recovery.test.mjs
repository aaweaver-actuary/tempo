// Real public CLI/assembly/admission/authority/configuration/runtime leaves.
// Only command/HTTP transport and the initial admission protocol are fixtures;
// these cases do not establish actual installed-target or cutover acceptance.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { main } from "../../scripts/tempo-cli.mjs";
import { createStudyStartupFixture } from "./fixtures/tempo-study-startup-fixture.mjs";

const composeOperation = (call, operation) => call.argv[2] === "compose" && call.argv.includes(operation);
const applicationStart = call => composeOperation(call, "up") && call.argv.includes("learning");
const learningHttp = call => call.argv.includes("exec") && call.argv.includes("-c")
  && call.argv.some(value => String(value).includes("/health/ready"));
const databaseReadback = call => call.argv.includes("tempo_learning_database.runtime_readback")
  && !call.argv.includes("--help");
const controlReadback = call => call.argv.some(value => String(value).includes("pg_controldata"));
const effects = fixture => fixture.calls.filter(call => ["up", "stop"].some(operation => composeOperation(call, operation)));
const invoke = (fixture, command, messages, services = []) => main(
  [command, ...services, "--config", fixture.admission.registrationPath], message => messages.push(message),
  { studyAssemblyDependencies: fixture.dependencies });
const publicationSnapshot = fixture => [fixture.admission.registrationPath, fixture.admission.receiptPath,
  fixture.admission.admissionPath, fixture.admission.originalRegistrationPath,
  fixture.registration.installedStudy.persistentIntentPath, fixture.registration.installedStudy.persistentReceiptPath]
  .map(path => ({ path, bytes: readFileSync(path) }));
const assertPreserved = (fixture, snapshot) => {
  for (const { path, bytes } of snapshot) assert.deepEqual(readFileSync(path), bytes, path);
  assert.equal(existsSync(join(fixture.admission.directory, "maintenance.lock")), false,
    "The retained original lock is released on success or failure");
  assert.equal(fixture.calls.some(call => call.argv.some(value => /^(?:build|pull|migrate|grant|DROP)$/.test(value))), false);
};
const markUnhealthy = fixture => {
  for (const container of Object.values(fixture.containers)) container.State.Running = true;
  fixture.containers.learning.State.Health = { Status: "unhealthy" };
};
const unavailable = () => ({ code: 1, stdout: "", stderr: "fixture: Learning readiness endpoint is unavailable" });

test("public-leaf health recovery stops an identified unhealthy Study target without requiring readiness", async t => {
  const fixture = await createStudyStartupFixture(t), messages = [];
  markUnhealthy(fixture);
  const before = publicationSnapshot(fixture);
  fixture.transport.beforeCommand = call => learningHttp(call) ? unavailable() : undefined;

  assert.equal(await invoke(fixture, "stop", messages), 0);

  const stopped = effects(fixture);
  assert.equal(stopped.length, 1);
  assert.equal(composeOperation(stopped[0], "stop"), true);
  assert.deepEqual(stopped[0].argv.slice(-2), ["study-web", "learning"]);
  assert.equal(stopped[0].lockHeld, true, "Stop retains the original authority lock");
  assert.equal(fixture.containers.postgres.State.Running, true, "Recovery does not stop the database owner");
  assert.equal(fixture.containers.learning.State.Running, false);
  assert.equal(fixture.containers["study-web"].State.Running, false);
  assert.equal(fixture.calls.some(call => learningHttp(call) || databaseReadback(call) || controlReadback(call)), false,
    "An availability check must not block stopping the independently identified target");
  assert.deepEqual(fixture.fetchCalls, []);
  assertPreserved(fixture, before);
});

test("public-leaf health recovery reads bounded logs from an identified unhealthy Study target", async t => {
  const fixture = await createStudyStartupFixture(t), messages = [];
  markUnhealthy(fixture);
  const before = publicationSnapshot(fixture);
  fixture.transport.beforeCommand = call => {
    if (learningHttp(call)) return unavailable();
    if (composeOperation(call, "logs")) return call.reply("fixture: Learning readiness is unavailable\n");
  };

  assert.equal(await invoke(fixture, "logs", messages, ["learning"]), 0);

  const logs = fixture.calls.filter(call => composeOperation(call, "logs"));
  assert.equal(logs.length, 1);
  assert.deepEqual(logs[0].argv.slice(logs[0].argv.indexOf("logs")), ["logs", "--no-color", "--tail", "100", "learning"]);
  assert.equal(logs[0].argv[logs[0].argv.indexOf("-p") + 1], fixture.registration.project);
  assert.equal(logs[0].settings.echo, true);
  assert.deepEqual(effects(fixture), []);
  assert.equal(fixture.calls.some(call => call.lockHeld || learningHttp(call) || databaseReadback(call) || controlReadback(call)), false,
    "Read-only logs need current target identity without a lock or service-readiness prerequisite");
  assert.deepEqual(fixture.fetchCalls, []);
  assert.ok(Object.values(fixture.containers).every(container => container.State.Running));
  assertPreserved(fixture, before);
});

test("public-leaf health recovery restarts an identified unhealthy target then verifies recovered health", async t => {
  const fixture = await createStudyStartupFixture(t), messages = [];
  markUnhealthy(fixture);
  const before = publicationSnapshot(fixture);
  let applicationsStarted = false;
  fixture.transport.beforeCommand = call => {
    if (applicationStart(call)) {
      applicationsStarted = true;
      fixture.containers.learning.State.Health = { Status: "healthy" };
    }
    if (learningHttp(call) && !applicationsStarted) return unavailable();
  };

  assert.equal(await invoke(fixture, "restart", messages), 0);

  const restarted = effects(fixture);
  assert.equal(restarted.length, 1, "Already running PostgreSQL is retained");
  assert.equal(applicationStart(restarted[0]), true);
  assert.equal(restarted[0].argv.includes("--force-recreate"), true);
  assert.deepEqual(restarted[0].argv.slice(-2), ["learning", "study-web"]);
  assert.equal(restarted[0].lockHeld, true);
  const startIndex = fixture.calls.indexOf(restarted[0]);
  assert.ok(fixture.calls.slice(0, startIndex).some(call => call.lockHeld && controlReadback(call)),
    "Restart preparation verifies the existing PostgreSQL identity before applications start");
  assert.ok(fixture.calls.slice(0, startIndex).some(call => call.lockHeld && databaseReadback(call)),
    "The public database contract must verify the exact target before application recreation");
  assert.ok(fixture.calls.slice(0, startIndex).some(call => call.lockHeld
    && call.argv.includes("tempo_learning_database.runtime_readback") && call.argv.includes("--help")),
    "Restart retains the installed-verifier preflight rather than skipping start preparation");
  const httpCalls = fixture.calls.filter(learningHttp);
  assert.ok(httpCalls.length > 0, "Successful restart requires post-start Learning health verification");
  assert.ok(httpCalls.every(call => call.lockHeld && fixture.calls.indexOf(call) > startIndex));
  assert.ok(fixture.calls.slice(startIndex + 1).some(call => call.lockHeld && databaseReadback(call)),
    "Post-start completion independently verifies the public database contract again");
  assert.equal(fixture.fetchCalls.length, 1, "The exact registered web ingress must also recover");
  assert.equal(fixture.containers.postgres.State.Running, true);
  assertPreserved(fixture, before);
});

test("public-leaf health recovery rejects restart completion when post-start Learning remains unhealthy", async t => {
  const fixture = await createStudyStartupFixture(t), messages = [];
  markUnhealthy(fixture);
  const before = publicationSnapshot(fixture);
  fixture.transport.beforeCommand = call => learningHttp(call)
    ? call.reply({ ready: { status: "unavailable", workspace_id: fixture.registration.cutover.workspace } }) : undefined;

  await assert.rejects(invoke(fixture, "restart", messages), error => error.code === "study_runtime_health_unverified");

  const restarted = effects(fixture);
  assert.equal(restarted.length, 1, "An unhealthy outcome never triggers an automatic retry or rollback");
  assert.equal(applicationStart(restarted[0]), true);
  assert.equal(restarted[0].lockHeld, true);
  const startIndex = fixture.calls.indexOf(restarted[0]), healthIndex = fixture.calls.findIndex(learningHttp);
  assert.ok(startIndex >= 0 && healthIndex > startIndex, "The failure is post-start health, not an initial availability gate");
  assert.ok(fixture.calls.slice(0, startIndex).some(call => call.lockHeld && databaseReadback(call)));
  assert.equal(fixture.calls[healthIndex].lockHeld, true);
  assert.equal(fixture.containers.postgres.State.Running, true);
  assert.equal(fixture.containers.learning.State.Running, true, "Preserve the current target for explicit forward recovery");
  assert.equal(fixture.containers["study-web"].State.Running, true);
  assert.deepEqual(fixture.fetchCalls, []);
  assertPreserved(fixture, before);
});

for (const command of ["stop", "logs", "restart"])
  test(`public-leaf health recovery ${command} rejects a different actual target image before effects or log access`, async t => {
    const fixture = await createStudyStartupFixture(t), messages = [];
    markUnhealthy(fixture);
    fixture.containers.learning.Image = `sha256:${"f".repeat(64)}`;
    const before = publicationSnapshot(fixture);
    fixture.transport.beforeCommand = call => {
      if (learningHttp(call)) return unavailable();
      if (composeOperation(call, "logs")) return call.reply("must not be read\n");
    };

    await assert.rejects(invoke(fixture, command, messages, command === "logs" ? ["learning"] : []),
      error => error.code === "study_runtime_container_mismatch");

    assert.deepEqual(effects(fixture), []);
    assert.equal(fixture.calls.some(call => composeOperation(call, "logs")), false);
    assert.equal(fixture.calls.some(call => call.lockHeld || learningHttp(call) || databaseReadback(call) || controlReadback(call)), false);
    assert.deepEqual(fixture.fetchCalls, []);
    assert.ok(Object.values(fixture.containers).every(container => container.State.Running));
    assertPreserved(fixture, before);
  });
