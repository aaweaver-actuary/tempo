import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { main } from "../../scripts/tempo-cli.mjs";
import { createStudyStartupFixture } from "./fixtures/tempo-study-startup-fixture.mjs";

// These cases compose the real public launcher leaves. Only command/HTTP
// transport and the initial observed-protocol packet are synthetic fixtures;
// they establish no actual target, database, graph or admission acceptance.
const argumentsFor = call => call.argv.slice(2);
const serviceEffects = calls => calls.filter(call => argumentsFor(call)[0] === "compose"
  && ["up", "stop"].some(operation => call.argv.includes(operation)));
const publicationFiles = fixture => [fixture.admission.registrationPath,
  fixture.admission.receiptPath, fixture.admission.admissionPath];
const publicationSnapshot = fixture => publicationFiles(fixture).map(path => ({ path, bytes: readFileSync(path) }));
const assertPublicationUnchanged = snapshot => {
  for (const { path, bytes } of snapshot) assert.deepEqual(readFileSync(path), bytes, path);
};
const executeStart = (fixture, messages) => main(["start", "--config", fixture.admission.registrationPath],
  message => messages.push(message), { studyAssemblyDependencies: fixture.dependencies });

test("public-leaf startup missing installed verifier blocks before PostgreSQL or application effects", async t => {
  const fixture = await createStudyStartupFixture(t), before = publicationSnapshot(fixture), messages = [];
  let verifierAttempted = false;
  fixture.transport.beforeCommand = ({ argv }) => {
    if (argv.includes("tempo_learning_database.runtime_readback") && argv.includes("--help")) {
      verifierAttempted = true;
      return { code: 1, stdout: "", stderr: "fixture: selected image has no installed verifier" };
    }
  };

  await assert.rejects(executeStart(fixture, messages), error => error.code === "study_runtime_probe_failed");

  assert.equal(verifierAttempted, true);
  assert.deepEqual(serviceEffects(fixture.calls), []);
  assert.equal(fixture.containers.postgres.State.Running, false);
  assert.equal(fixture.containers.learning.State.Running, false);
  assert.equal(fixture.containers["study-web"].State.Running, false);
  assert.deepEqual(fixture.fetchCalls, []);
  assertPublicationUnchanged(before);
  assert.equal(existsSync(join(fixture.admission.directory, "maintenance.lock")), false);
});

test("public-leaf startup failed readback after PostgreSQL start preserves that target without opening applications or rewriting admission", async t => {
  const fixture = await createStudyStartupFixture(t), before = publicationSnapshot(fixture), messages = [];
  let readbackAttempted = false;
  fixture.transport.beforeCommand = ({ argv }) => {
    if (argv.includes("tempo_learning_database.runtime_readback") && !argv.includes("--help")) {
      readbackAttempted = true;
      assert.equal(fixture.containers.postgres.State.Running, true);
      return { code: 1, stdout: "", stderr: "fixture: restricted runtime readback did not verify" };
    }
  };

  await assert.rejects(executeStart(fixture, messages), error => error.code === "study_runtime_probe_failed");

  assert.equal(readbackAttempted, true);
  const effects = serviceEffects(fixture.calls);
  assert.equal(effects.length, 1, "Only the selected existing PostgreSQL service may have started");
  assert.equal(effects[0].argv.includes("up"), true);
  assert.equal(effects[0].argv.at(-1), "postgres");
  assert.equal(effects[0].argv[effects[0].argv.indexOf("-p") + 1], fixture.registration.project);
  assert.equal(fixture.containers.postgres.State.Running, true, "No automatic rollback of the selected target");
  assert.equal(fixture.containers.learning.State.Running, false);
  assert.equal(fixture.containers["study-web"].State.Running, false);
  assert.deepEqual(fixture.fetchCalls, []);
  assertPublicationUnchanged(before);
  assert.equal(existsSync(join(fixture.admission.directory, "maintenance.lock")), false);
});

test("public-leaf startup reobserves original writer fencing under the retained lock before PostgreSQL or applications start", async t => {
  const fixture = await createStudyStartupFixture(t), before = publicationSnapshot(fixture), messages = [];
  const sourceProject = fixture.admission.plan.original.registration.project;
  const lockPath = join(fixture.admission.directory, "maintenance.lock"), writerId = "d".repeat(64);
  let lockedSourceInspection = false;
  fixture.transport.beforeCommand = ({ argv, reply }) => {
    const args = argv.slice(2);
    if (!existsSync(lockPath)) return;
    if (args[0] === "ps" && args.includes(`label=com.docker.compose.project=${sourceProject}`))
      return reply(writerId);
    if (args[0] === "inspect" && args.length === 2 && args[1] === writerId) {
      lockedSourceInspection = true;
      return reply([{ Id: writerId, Config: { Labels: {
        "com.docker.compose.project": sourceProject, "com.docker.compose.service": "api",
      } }, State: { Running: true, Restarting: false }, HostConfig: { RestartPolicy: { Name: "no" } }, Mounts: [] }]);
    }
  };

  await assert.rejects(executeStart(fixture, messages), error => error.code === "study_original_writer_unfenced");

  assert.equal(lockedSourceInspection, true, "The real authority reader must inspect the changed source under its original lock");
  assert.deepEqual(serviceEffects(fixture.calls), []);
  assert.equal(fixture.containers.postgres.State.Running, false);
  assert.equal(fixture.containers.learning.State.Running, false);
  assert.equal(fixture.containers["study-web"].State.Running, false);
  assert.deepEqual(fixture.fetchCalls, []);
  assertPublicationUnchanged(before);
  assert.equal(existsSync(lockPath), false);
});
