import assert from "node:assert/strict";
import { mkdirSync, readFileSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { evaluateFreshStartReadiness, readinessBinding } from "../../scripts/tempo-cutover-readiness.mjs";
import { createCutoverState, cutoverReleaseBinding,
  executeFreshStartTransition, verifyInstalledAdmissionPacket } from "../../scripts/tempo-cutover-transition.mjs";

// Observed-PROTOCOL fixtures backed only by injected fakes and disposable files.
// These tests observe no installed application, source database or user approval.
import { createAdmissionProtocolFixture as fixture } from "./fixtures/tempo-study-admission-fixture.mjs";
const run = f => executeFreshStartTransition(f.plan, { state: f.state, effects: f.effects() });
const rejectsCode = (operation, expected) => assert.rejects(operation, error => error.code === expected);

test("installed producer completes only after durable publication and observed admission, then projects the existing public packet", async t => {
  const f = fixture(t); await run(f);
  const packet = f.state.readCompletedAdmission(f.plan), authority = verifyInstalledAdmissionPacket(packet);
  assert.equal(authority.phase, "complete"); assert.equal(authority.scope, "observed");
  assert.equal(authority.sourceIdentity, f.plan.sourceIdentity); assert.equal(authority.lockIdentity, f.plan.lockIdentity);
  assert.deepEqual(authority.binding, readinessBinding(packet.plan, "before-admission", packet.evidence.binding.gateContext));
  assert.equal(packet.facts.publication.durable, true); assert.equal(packet.facts.authorityFence.originalFenced, true);
  assert.equal(f.infra.originalWriter, false); assert.equal(f.infra.targetWriter, true);
  assert.deepEqual(JSON.parse(readFileSync(f.admissionPath, "utf8")), packet);
  assert.equal(statSync(f.admissionPath).mode & 0o777, 0o600);
  assert.ok(f.events.indexOf("before-authority-switch") < f.events.indexOf("fence"));
  assert.ok(f.events.indexOf("before-admission") < f.events.indexOf("admit"));
  packet.facts.admission.phase = "mutated-caller-copy";
  assert.equal(f.state.readCompletedAdmission(f.plan).facts.admission.phase, "complete");
});

test("a raw observed executionScope label does not opt into installed effects", async t => {
  const f = fixture(t);
  await rejectsCode(executeFreshStartTransition(f.plan, { state: f.state, effects: { executionScope: "installed", ...f.operations } }), "graph-contract-pending");
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("copying a nominal installed capability object loses installed execution authority", async t => {
  const f = fixture(t), effects = { ...f.effects() };
  await rejectsCode(executeFreshStartTransition(f.plan, { state: f.state, effects }), "graph-contract-pending");
  assert.equal(f.state.readJournal(), null);
});
test("missing public operations block before any target mutation", t => {
  const f = fixture(t); delete f.operations.fenceOriginalAuthority;
  assert.throws(() => f.effects(), error => error.code === "installed_effect_contract_pending");
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("installed capability snapshots owner function references without shared mutable operation ownership", async t => {
  const f = fixture(t), effects = f.effects(); f.operations.admitTarget = () => { throw Error("mutated external callback"); };
  await executeFreshStartTransition(f.plan, { state: f.state, effects });
  assert.equal(f.state.readJournal().phase, "complete");
});
test("disposable rehearsal identity cannot be promoted into installed admission", async t => {
  const f = fixture(t); f.plan.registration.disposable = true;
  await rejectsCode(run(f), "installed_target_required"); assert.equal(f.state.readJournal(), null);
});
test("the retained synthetic readiness export still blocks observed evidence", t => {
  const f = fixture(t), evidence = f.config.readReleaseEvidence({ plan: f.plan, checkpoint: "before-preparation", gateContext: "fixture" });
  const result = evaluateFreshStartReadiness({ plan: f.plan, checkpoint: "before-preparation", gateContext: "fixture", evidence }, f.adapters);
  assert.equal(result.status, "blocked"); assert.equal(result.liveExecutionAllowed, false); assert.equal(result.scope, "synthetic-fixture");
});
test("the retained synthetic readiness protocol remains pure ready with production blocked", t => {
  const f = fixture(t), evidence = f.config.readReleaseEvidence({ plan: f.plan, checkpoint: "before-preparation", gateContext: "fixture" });
  evidence.scope = "synthetic-fixture";
  const adapter = { scope: "synthetic-fixture", validate: (input, binding) => ({ status: "verified", binding }) };
  const result = evaluateFreshStartReadiness({ plan: f.plan, checkpoint: "before-preparation", gateContext: "fixture", evidence },
    { graphAdapter: adapter, freshInitializationAdapter: adapter });
  assert.equal(result.status, "ready"); assert.equal(result.production.status, "blocked"); assert.equal(result.liveExecutionAllowed, false);
});
test("installed readiness requires separately authorized exact target even before preparation", async t => {
  const f = fixture(t), evidence = f.config.readReleaseEvidence;
  f.config.readReleaseEvidence = args => ({ ...evidence(args), cutoverConfirmation: null });
  await rejectsCode(run(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal(), null);
  assert.ok(!f.events.includes("source"));
});
test("installed readiness cannot promote synthetic provider verifiers", async t => {
  const f = fixture(t); f.adapters.graphAdapter = { ...f.adapters.graphAdapter, scope: "synthetic-fixture" };
  await rejectsCode(run(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal(), null);
});
test("installed readiness requires observed verifier result provenance beyond a positive status", async t => {
  const f = fixture(t); f.adapters.graphAdapter = { scope: "observed", validate: (value, binding) => ({ status: "verified", binding }) };
  await rejectsCode(run(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal(), null);
});
test("a completed synthetic journal cannot be promoted by adding installed execution scope", async t => {
  const f = fixture(t);
  const effects = { ...f.operations, executionScope: "synthetic-fixture",
    assertReleaseGates: async (plan, checkpoint, facts, gateContext) => ({ status: "ready", evidenceId: "fixture-gate",
      binding: cutoverReleaseBinding(plan, checkpoint, gateContext) }) };
  await executeFreshStartTransition(f.plan, { state: f.state, effects });
  assert.throws(() => f.state.readCompletedAdmission(f.plan), error => error.code === "installed_admission_pending");
  const journal = f.state.readJournal(); journal.executionScope = "installed"; f.state.saveJournal(journal);
  assert.throws(() => f.state.readCompletedAdmission(f.plan), error => error.code === "installed_effect_unverified");
});
test("mismatched final graph receipt blocks before original writer fencing", async t => {
  const f = fixture(t), operation = f.operations.importGraph;
  f.operations.importGraph = async args => ({ ...await operation(args), graphReceipt: "wrong-receipt" });
  await rejectsCode(run(f), "fresh_start_gate_blocked");
  assert.equal(f.infra.originalWriter, true); assert.equal(f.infra.targetWriter, false);
});
test("failed publication durability never enables installed target traffic", async t => {
  const f = fixture(t); f.state.confirmPublicationDurability = () => ({ durable: false, evidenceId: "not-durable" });
  await rejectsCode(run(f), "fresh_start_publication_not_durable");
  assert.equal(f.infra.targetWriter, false); assert.ok(!f.events.includes("admit"));
});
test("installed finalizer cannot append or replace default configuration after the selected plan", async t => {
  const f = fixture(t), operation = f.operations.finalizeFreshStartPublication;
  f.operations.finalizeFreshStartPublication = async args => {
    const final = await operation(args); final.registration.installedStudy = { injected: true }; return final;
  };
  await rejectsCode(run(f), "fresh_start_identity_changed"); assert.equal(f.infra.originalWriter, true);
});
test("missing completed-admission destination blocks before any installed owner operation", async t => {
  const f = fixture(t), state = createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath,
    receiptPath: f.receiptPath, lockIdentity: f.plan.lockIdentity });
  await rejectsCode(executeFreshStartTransition(f.plan, { state, effects: f.effects() }), "installed_admission_destination_pending");
  assert.equal(state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("a private but unpinned completed-admission destination is rejected before effects", async t => {
  const f = fixture(t), state = createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath,
    receiptPath: f.receiptPath, lockIdentity: f.plan.lockIdentity, admissionPath: join(f.directory, "different-admission.json") });
  await rejectsCode(executeFreshStartTransition(f.plan, { state, effects: f.effects() }), "installed_admission_carrier_mismatch");
  assert.equal(state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("original metadata cannot point to the registration that publication will rewrite", async t => {
  const f = fixture(t); f.plan.registration.installedStudy.originalRegistrationPath = f.registrationPath;
  await rejectsCode(run(f), "installed_original_metadata_invalid"); assert.equal(f.state.readJournal(), null);
});
test("missing preserved original metadata is an explicit pre-effect blocker", async t => {
  const f = fixture(t); f.plan.registration.installedStudy.originalRegistrationPath = join(f.directory, "not-preserved.json");
  await rejectsCode(run(f), "installed_original_metadata_invalid"); assert.equal(f.state.readJournal(), null);
});
test("mismatched preserved original metadata cannot qualify the pinned original authority", async t => {
  const f = fixture(t); writeFileSync(f.originalRegistrationPath, JSON.stringify({ version: 1, project: "wrong-source" }), { mode: 0o600 });
  await rejectsCode(run(f), "installed_original_metadata_mismatch"); assert.equal(f.state.readJournal(), null);
});
test("admission output cannot overlap registration before any installed owner operation", async t => {
  const f = fixture(t); f.plan.registration.installedStudy.admissionPath = f.registrationPath;
  const state = createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath,
    receiptPath: f.receiptPath, lockIdentity: f.plan.lockIdentity, admissionPath: f.registrationPath });
  await rejectsCode(executeFreshStartTransition(f.plan, { state, effects: f.effects() }), "installed_admission_destination_invalid");
  assert.equal(state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("admission output inside the selected checkout is refused before installed effects", async t => {
  const f = fixture(t); f.plan.registration.root = f.directory;
  await rejectsCode(run(f), "installed_admission_destination_invalid");
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("a dot-prefixed child directory cannot bypass the protected checkout boundary", async t => {
  const f = fixture(t), parent = join(f.directory, "..private-admission"); mkdirSync(parent, { mode: 0o700 });
  f.plan.registration.root = f.directory;
  f.plan.registration.installedStudy.admissionPath = join(parent, "admission.json");
  const state = createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath,
    receiptPath: f.receiptPath, lockIdentity: f.plan.lockIdentity, admissionPath: join(parent, "admission.json") });
  await rejectsCode(executeFreshStartTransition(f.plan, { state, effects: f.effects() }), "installed_admission_destination_invalid");
  assert.equal(state.readJournal(), null); assert.deepEqual(f.events, []);
});
test("an admission output symlink never rebinds an existing publication", async t => {
  const f = fixture(t), prior = readFileSync(f.registrationPath, "utf8"); symlinkSync(f.registrationPath, f.admissionPath);
  await rejectsCode(run(f), "installed_admission_destination_invalid");
  assert.equal(readFileSync(f.registrationPath, "utf8"), prior); assert.equal(f.state.readJournal(), null);
});
test("an existing unrelated admission output is preserved rather than overwritten", async t => {
  const f = fixture(t); writeFileSync(f.admissionPath, "{}\n", { mode: 0o600 });
  await rejectsCode(run(f), "installed_admission_destination_mismatch");
  assert.equal(readFileSync(f.admissionPath, "utf8"), "{}\n"); assert.equal(f.state.readJournal(), null);
});
test("completed resume republishes a failed admission output without repeating accepted operations", async t => {
  const f = fixture(t), write = f.state.writeCompletedAdmission;
  f.state.writeCompletedAdmission = () => { throw Error("fixture packet write interrupted"); };
  await assert.rejects(run(f), /packet write interrupted/);
  assert.equal(f.state.readJournal().phase, "complete"); const count = f.events.length;
  f.state.writeCompletedAdmission = write; await run(f);
  assert.equal(f.events.length, count); assert.equal(JSON.parse(readFileSync(f.admissionPath, "utf8")).facts.admission.phase, "complete");
  const prior = statSync(f.admissionPath).mtimeMs; await run(f);
  assert.equal(statSync(f.admissionPath).mtimeMs, prior); assert.equal(f.events.length, count);
});
for (const [name, change] of [
  ["synthetic stage", fact => { fact.scope = "synthetic-fixture"; }],
  ["empty evidence", fact => { fact.evidenceId = " "; }],
  ["wrong stage binding", fact => { fact.binding.planSha256 = "0".repeat(64); }],
]) test(`installed producer rejects ${name} before original authority changes`, async t => {
  const f = fixture(t), operation = f.operations.verifyGraphSource;
  f.operations.verifyGraphSource = async args => { const fact = await operation(args); change(fact); return fact; };
  await rejectsCode(run(f), "installed_effect_unverified");
  assert.equal(f.infra.originalWriter, true); assert.equal(f.infra.targetWriter, false);
});
test("installed projection refuses a durable matching publication without actual completed admission", async t => {
  const f = fixture(t); f.operations.verifyAdmission = async args => ({ scope: "observed", verified: true,
    evidenceId: "fixture-only", binding: args.binding, operationBinding: args.operationBinding, phase: "pending" });
  await rejectsCode(run(f), "installed_admission_unverified");
  assert.equal(f.state.readJournal().phase, "activation-started");
  assert.throws(() => f.state.readCompletedAdmission(f.plan), error => error.code === "installed_admission_pending");
});
test("installed projection refuses split publication after completed readback", async t => {
  const f = fixture(t); await run(f); writeFileSync(f.receiptPath, "{}", { mode: 0o600 });
  assert.throws(() => f.state.readCompletedAdmission(f.plan), error => error.code === "fresh_start_publication_mismatch");
});
test("failed admission preserves activation-started and requires explicit forward recovery, never original fallback", async t => {
  const f = fixture(t), admit = f.operations.admitTarget;
  f.operations.admitTarget = async args => { await admit(args); throw Error("fixture uncertain acceptance"); };
  await assert.rejects(run(f), /uncertain acceptance/);
  await rejectsCode(run(f), "fresh_start_forward_recovery_required");
  assert.equal(f.infra.originalWriter, false); assert.equal(f.state.readJournal().phase, "activation-started");
  // Independent fixture operator models target quarantine before explicit repair.
  f.infra.targetWriter = false;
  await executeFreshStartTransition(f.plan, { state: f.state, effects: f.effects(), recovery: "forward" });
  assert.equal(f.state.readCompletedAdmission(f.plan).facts.admission.phase, "complete");
  assert.ok(f.events.includes("forward-repair")); assert.equal(f.infra.originalWriter, false);
});
for (const [name, alter] of [
  ["synthetic completed fact", packet => { packet.facts.admission.scope = "synthetic-fixture"; }],
  ["confirmation only", packet => { delete packet.facts.admission; }],
  ["wrong final binding", packet => { packet.facts.admission.binding.planSha256 = "0".repeat(64); }],
  ["wrong source identity", packet => { packet.plan.sourceIdentity = "wrong-source"; }],
  ["wrong graph receipt", packet => { packet.facts.graphImported.graphReceipt = "wrong-graph"; }],
  ["non-durable publication", packet => { packet.facts.publication.durable = false; }],
  ["missing isolation", packet => { delete packet.evidence.authorityIsolation; }],
  ["missing exact confirmation", packet => { packet.evidence.cutoverConfirmation.confirmed = false; }],
  ["missing observed fence", packet => { packet.facts.authorityFence.scope = "synthetic-fixture"; }],
  ["wrong completion operation", packet => { packet.facts.admission.operationBinding.checkpoint = "confirmation-only"; }],
  ["missing stage provenance", packet => { delete packet.facts.graphSource.binding; }],
  ["tampered finalized authority-fence plan digest", packet => { packet.facts.authorityFence.binding.planSha256 = "0".repeat(64); }],
]) test(`public admission validator rejects ${name}`, async t => {
  const f = fixture(t); await run(f); const packet = f.state.readCompletedAdmission(f.plan); alter(packet);
  assert.throws(() => verifyInstalledAdmissionPacket(packet));
});
