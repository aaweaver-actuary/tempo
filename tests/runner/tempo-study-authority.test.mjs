import test from "node:test";
import assert from "node:assert/strict";
import { createInstalledAuthorityReader, observeOriginalStudyFence } from "../../scripts/tempo-study-authority.mjs";
import { targetKey } from "../../scripts/tempo-deployment.mjs";
import { readinessBinding } from "../../scripts/tempo-cutover-readiness.mjs";

// All observed protocol values below come from injected fakes, not a real host.
const clone = value => structuredClone(value);
function fixture() {
  const original = { version: 1, context: "source-context", daemonId: "source-daemon", project: "old-study",
    volumes: { database: { name: "original-data" } }, postgresVolumeKey: "database" };
  const registration = { version: 2, context: "fresh-context", daemonId: "fresh-daemon", project: "fresh-study",
    volumes: { database: { name: "fresh-data" }, private: { name: "fresh-private" } },
    installedStudy: { version: 1, runtime: { version: 1, postgres: { volumeKey: "database", volumeName: "fresh-data",
      mountDestination: "/var/lib/postgresql", dataDirectory: "/var/lib/postgresql/18/docker", major: 18,
      systemIdentifier: "123456789" }, credentialDigests: { studyTokenSha256: "a".repeat(64), runtimePassfileSha256: "b".repeat(64) } } },
    cutover: { mode: "graph-preserving-fresh-start", phase: "quarantined", transitionId: "fixture-transition",
      workspace: "fresh-workspace", schema: `sha256:${"c".repeat(64)}`, revision: "d".repeat(40),
      graphSourceReference: "fixture-graph-source", graphReceipt: "fixture-graph", freshInitReceipt: "fixture-init",
      ingress: { origin: "http://127.0.0.1:38127" }, images: { postgres: `sha256:${"e".repeat(64)}` } } };
  const receipt = { version: 2, revision: registration.cutover.revision, schema: registration.cutover.schema,
    cutover: clone(registration.cutover), images: clone(registration.cutover.images) };
  const plan = { version: 1, transitionId: "fixture-transition", revision: registration.cutover.revision,
    freshStartChoice: "graph-preserving-fresh-start", sourceIdentity: targetKey(original), lockIdentity: targetKey(original),
    targetIdentity: targetKey(registration), original: { registration: clone(original), receipt: null },
    registration: clone(registration), receipt: clone(receipt) };
  const binding = readinessBinding(plan, "before-admission", "fixture-original-cutover-invocation");
  const admission = { plan, evidence: { scope: "observed", binding },
    facts: { admission: { scope: "observed", verified: true, phase: "complete", evidenceId: "fixture-owned-completion", binding } } };
  const events = [], containers = [
    { Id: "1".repeat(64), Config: { Labels: { "com.docker.compose.project": original.project, "com.docker.compose.service": "api" } },
      State: { Running: false, Restarting: false }, HostConfig: { RestartPolicy: { Name: "unless-stopped" } }, Mounts: [] },
    { Id: "2".repeat(64), Config: { Labels: { "com.docker.compose.project": original.project, "com.docker.compose.service": "postgres" } },
      State: { Running: true, Restarting: false }, HostConfig: { RestartPolicy: { Name: "always" } },
      Mounts: [{ Type: "volume", Name: "original-data" }] },
  ];
  const controls = { sourceDaemon: original.daemonId, targetDaemon: registration.daemonId, containers };
  const request = { registration, command: "status", configPath: "/private/registration.json", flags: [], services: [] };
  const ports = {
    originalRegistration: original, admission,
    async validateAdmission(packet) {
      events.push("admission");
      return { ...clone(packet.facts.admission), sourceIdentity: packet.plan.sourceIdentity,
        lockIdentity: packet.plan.lockIdentity, targetIdentity: packet.plan.targetIdentity, transitionId: packet.plan.transitionId };
    },
    async readPublication(input) { events.push("publication"); return { registration: clone(input.registration), receipt: clone(receipt) }; },
    async inspectConfiguration() { events.push("configuration"); return { name: "fresh-study", services: { learning: {}, postgres: {}, "study-web": {} } }; },
    async inspectRuntime(input) { events.push("runtime"); return { status: "verified", scope: "observed", evidenceId: "fixture-runtime-metadata",
      binding: clone(input), state: "cold", runtime: clone(input.registration.installedStudy.runtime),
      clusterIdentityVerified: false, schemaVerified: false, healthVerified: false }; },
    async run(command, args, settings) {
      events.push({ command, args: clone(args), settings: clone(settings) });
      assert.equal(command, "docker");
      assert.equal(settings.echo, false);
      if (args[2] === "info") return { code: 0, stdout: `${args[1] === "source-context" ? controls.sourceDaemon : controls.targetDaemon}\n` };
      assert.equal(args[1], "source-context");
      if (args[2] === "ps") return { code: 0, stdout: controls.containers.map(c => c.Id.slice(0, 12)).join("\n") };
      if (args[2] === "inspect") return { code: 0, stdout: JSON.stringify(controls.containers) };
      throw new Error(`Unexpected effect: ${args[2]}`);
    },
  };
  return { original, registration, receipt, plan, admission, request, events, ports, controls };
}
const rejectsCode = (operation, code) => assert.rejects(operation, error => error.code === code);

test("installed authority composes current source fence, exact publication and honest cold runtime through public ports", async () => {
  const f = fixture(), read = createInstalledAuthorityReader(f.ports), result = await read(f.request);
  assert.equal(result.status, "verified"); assert.equal(result.scope, "observed");
  assert.equal(result.authority.phase, "complete"); assert.equal(result.authority.lockIdentity, targetKey(f.original));
  assert.equal(result.authority.originalWriterFenced, true); assert.equal(result.runtimeInspection.state, "cold");
  assert.equal(result.runtimeInspection.clusterIdentityVerified, false); assert.equal(result.runtimeInspection.schemaVerified, false);
  assert.equal(result.runtimeInspection.healthVerified, false); assert.equal(result.receipt.cutover.phase, "quarantined");
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.runtime));
  assert.deepEqual(result.binding, f.request);
  const commands = f.events.filter(event => typeof event === "object");
  assert.deepEqual(commands.map(event => event.args[2]), ["info", "info", "ps", "inspect"]);
});

test("installed authority missing completed admission producer blocks before any host observation", async () => {
  const f = fixture(); delete f.ports.validateAdmission;
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "production-admission-contract-pending");
  assert.deepEqual(f.events, []);
});

for (const [name, alter] of [
  ["synthetic completion", fact => { fact.scope = "synthetic-fixture"; }],
  ["confirmation without completion", fact => { fact.phase = "authorized"; }],
  ["unverified completion", fact => { fact.verified = false; }],
  ["blank producer evidence", fact => { fact.evidenceId = " "; }],
  ["another plan", fact => { fact.binding.planSha256 = "0".repeat(64); }],
  ["another source lock", fact => { fact.lockIdentity = "different"; }],
  ["another target", fact => { fact.targetIdentity = "different"; }],
]) test(`installed authority rejects ${name} before Docker`, async () => {
  const f = fixture(), original = f.ports.validateAdmission;
  f.ports.validateAdmission = async packet => { const fact = await original(packet); alter(fact); return fact; };
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_admission_incomplete");
  assert.deepEqual(f.events, ["admission"]);
});

test("installed authority rejects changed planned carrier and registration before observation", async () => {
  const f = fixture(); f.request.registration.installedStudy.runtime.postgres.major = 19;
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_admission_plan_mismatch");
  assert.deepEqual(f.events, []);
});

test("installed authority rejects the fresh key substituted for the retained original lock", async () => {
  const f = fixture(); f.admission.plan.lockIdentity = f.plan.targetIdentity;
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_admission_plan_mismatch");
  assert.deepEqual(f.events, []);
});

test("installed authority source and target volumes stay independent", async () => {
  const f = fixture(); f.registration.volumes.database.name = "original-data";
  f.plan.registration = clone(f.registration); f.plan.targetIdentity = targetKey(f.registration);
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_authority_volume_reuse");
  assert.deepEqual(f.events, []);
});

test("installed authority partial or stale deployment publication blocks before Docker", async () => {
  const f = fixture(); f.ports.readPublication = async () => ({ registration: clone(f.registration), receipt: null });
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_publication_mismatch");
  assert.deepEqual(f.events, ["admission"]);
});

for (const key of ["readPublication", "inspectConfiguration", "inspectRuntime", "run"])
  test(`installed authority missing ${key} is an explicit observer blocker`, async () => {
    const f = fixture(); delete f.ports[key];
    await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_installed_observers_pending");
    assert.deepEqual(f.events, ["admission"]);
  });

test("installed authority verifies current target daemon instead of trusting context name", async () => {
  const f = fixture(); f.controls.targetDaemon = "rebound-context";
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_target_daemon_mismatch");
  assert.equal(f.events.includes("runtime"), false);
});

test("installed authority verifies original daemon instead of a new target lock or project alias", async () => {
  const f = fixture(); f.controls.sourceDaemon = "rebound-source";
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_source_daemon_mismatch");
  assert.equal(f.events.includes("runtime"), false);
});

for (const [name, alter] of [
  ["running source writer", container => { container.State.Running = true; }],
  ["restarting source writer", container => { container.State.Restarting = true; }],
  ["source writer auto-reopens after daemon restart", container => { container.HostConfig.RestartPolicy.Name = "always"; }],
  ["unidentified source writer policy", container => { delete container.HostConfig.RestartPolicy; }],
]) test(`installed authority refuses ${name}`, async () => {
  const f = fixture(); alter(f.controls.containers[0]);
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_original_writer_unfenced");
  assert.equal(f.events.includes("configuration"), false);
});

test("source PostgreSQL exception requires its exact original volume rather than service label", async () => {
  const f = fixture(); f.controls.containers[1].Mounts[0].Name = "unrelated-data";
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_original_writer_unfenced");
});

test("an absent original project remains fenced without requiring its cold infrastructure to run", async () => {
  const f = fixture(); f.controls.containers = [];
  const result = await createInstalledAuthorityReader(f.ports)(f.request);
  assert.equal(result.sourceFence.verified, true);
  assert.deepEqual(f.events.filter(event => typeof event === "object").map(event => event.args[2]), ["info", "info", "ps"]);
});

test("source observation rejects unbound inspect result rather than accepting an empty writer list", async () => {
  const f = fixture(), run = f.ports.run;
  f.ports.run = async (command, args, settings) => args[2] === "inspect" ? { code: 0, stdout: "[]" } : run(command, args, settings);
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_source_observation_invalid");
});

test("source observation failure is fail-closed and does not expose raw command evidence", async () => {
  const f = fixture(); f.ports.run = async () => { throw new Error("private-payload-must-not-escape"); };
  await assert.rejects(() => observeOriginalStudyFence(f.original, { run: f.ports.run }), error =>
    error.code === "study_source_observation_failed" && !error.message.includes("private-payload"));
});

for (const [name, alter] of [
  ["synthetic runtime", result => { result.scope = "synthetic-fixture"; }],
  ["unverified runtime", result => { result.status = "pending"; }],
  ["wrong request", result => { result.binding.command = "different"; }],
  ["different physical descriptor", result => { result.runtime.postgres.systemIdentifier = "different"; }],
]) test(`installed authority rejects ${name}`, async () => {
  const f = fixture(), inspect = f.ports.inspectRuntime;
  f.ports.inspectRuntime = async input => { const result = await inspect(input); alter(result); return result; };
  await rejectsCode(() => createInstalledAuthorityReader(f.ports)(f.request), "study_runtime_lineage_unverified");
});

test("installed authority snapshots original proof inputs without shared mutable ownership", async () => {
  const f = fixture(), read = createInstalledAuthorityReader(f.ports);
  f.original.project = "mutated"; f.admission.facts.admission.evidenceId = "mutated";
  const result = await read(f.request);
  assert.equal(result.authority.evidenceId, "fixture-owned-completion");
  assert.equal(result.authority.lockIdentity, f.plan.sourceIdentity);
});
