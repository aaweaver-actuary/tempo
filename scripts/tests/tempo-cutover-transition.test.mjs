import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { acquireTargetLock } from "../tempo-deployment.mjs";
import { createCutoverState, cutoverReleaseBinding, executeCutoverTransition, restoreOriginalPublication } from "../tempo-cutover-transition.mjs";

// Deliberately synthetic. These identifiers are not installation observations,
// real-history acceptance, or permission to execute against the real target.
function fixture(context, { absentReceipt = false } = {}) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-cutover-fixture-"));
  context.after(() => rmSync(directory, { recursive: true, force: true }));
  const registrationPath = join(directory, "registration.json"), receiptPath = join(directory, "deployment.json");
  const registrationBytes = Buffer.from(' { "version": 1, "project": "synthetic-legacy" }\r\n');
  const receiptBytes = absentReceipt ? null : Buffer.from('{"revision":"synthetic-old","version":1}\n\n');
  writeFileSync(registrationPath, registrationBytes, { mode: 0o600 });
  if (receiptBytes) writeFileSync(receiptPath, receiptBytes, { mode: 0o600 });
  const identity = { transitionId: "fixture-transition", phase: "quarantined", revision: "synthetic-selected-revision",
    images: { web: "sha256:synthetic-web", learning: "sha256:synthetic-learning" },
    database: { name: "synthetic-learning", volume: "fixture-volume" }, workspace: "fixture-workspace",
    composeFiles: ["synthetic-study-compose.yml"], volumes: { data: { name: "fixture-volume", external: true } },
    ingress: { origin: "https://fixture.invalid", hosts: ["fixture.invalid"] }, schema: 1, transferReceipt: null };
  const plan = { version: 1, transitionId: identity.transitionId, revision: identity.revision,
    sourceIdentity: "fixture-source-key", targetIdentity: "fixture-target-key", lockIdentity: "fixture-source-key",
    registration: { version: 2, project: "fixture-target", cutover: structuredClone(identity) },
    receipt: { version: 2, revision: identity.revision, cutover: structuredClone(identity) },
    original: { registration: JSON.parse(registrationBytes), receipt: receiptBytes ? JSON.parse(receiptBytes) : null } };
  const state = createCutoverState({ stateDirectory: directory, registrationPath, receiptPath, lockIdentity: plan.lockIdentity });
  const events = [], completed = new Map();
  const infrastructure = { legacyWriter: true, targetWriter: false, maintenance: false, targetEffects: "none" };
  const observed = name => { events.push(name); assert.ok(state.readJournal(), "intent must be durable before side effects"); };
  const result = (name, facts = {}) => ({ verified: true, evidenceId: `fixture-${name}`, ...facts });
  const effect = (name, run) => async args => {
    observed(name); const evidence = run(args); completed.set(args.operationKey, structuredClone(evidence)); return evidence;
  };
  const effects = {
    executionScope: "synthetic-fixture",
    assertReleaseGates: async (selected, checkpoint, facts, gateContext) => {
      events.push(checkpoint);
      if (!["before-fence", "before-resume"].includes(checkpoint)) {
        assert.equal(facts.sourceSnapshot.sourceStateId, "fixture-final-fenced-source");
        assert.equal(facts.imported.sourceStateId, facts.sourceSnapshot.sourceStateId);
        assert.equal(selected.receipt.cutover.transferReceipt, "fixture-final-transfer");
        if (checkpoint === "before-activation") assert.equal(facts.publication.durable, true);
      }
      return { status: "ready", evidenceId: `fixture-gate-${checkpoint}`, binding: cutoverReleaseBinding(selected, checkpoint, gateContext) };
    },
    verifyResumptionFence: async ({ gateContext }) => ({ verified: true, evidenceId: "fixture-fresh-fence", gateContext,
      legacyFenced: !infrastructure.legacyWriter, targetQuarantined: !infrastructure.targetWriter, originalBrowserWorkRecoverable: true }),
    fenceLegacy: effect("fence", () => { infrastructure.legacyWriter = false; return result("fence"); }),
    snapshotSource: effect("snapshot", () => {
      assert.equal(infrastructure.legacyWriter, false);
      return result("source", { sourceStateId: "fixture-final-fenced-source", restoreVerified: true });
    }),
    importHistory: effect("import", ({ facts }) => result("import", { sourceStateId: facts.sourceSnapshot.sourceStateId,
      transferReceipt: { fixtureOnly: true, id: "fixture-final-transfer" } })),
    quarantineTarget: effect("quarantine", () => result("quarantine", { targetIdentity: plan.targetIdentity,
      workspace: identity.workspace, schema: identity.schema, readOnly: true, maintenanceDisabled: true, mutatingTrafficDisabled: true })),
    backupTarget: effect("target-backup", ({ facts }) => result("target-backup", { targetIdentity: plan.targetIdentity,
      sourceStateId: facts.sourceSnapshot.sourceStateId, restoreVerified: true })),
    finalizePublication: async ({ plan: selected, transferReceipt }) => {
      events.push("finalize"); assert.equal(transferReceipt.id, "fixture-final-transfer");
      const registration = structuredClone(selected.registration), receipt = structuredClone(selected.receipt);
      registration.cutover.transferReceipt = transferReceipt.id; receipt.cutover.transferReceipt = transferReceipt.id;
      return { registration, receipt, transferAcceptance: { fixtureOnly: true, id: transferReceipt.id } };
    },
    activateTarget: async ({ plan: selected }) => {
      observed("activate"); assert.equal(state.readJournal().phase, "activation-started");
      assert.equal(selected.receipt.cutover.transferReceipt, "fixture-final-transfer");
      assert.equal(infrastructure.legacyWriter, false); infrastructure.targetWriter = true; infrastructure.maintenance = true;
    },
    verifyActivation: async () => result("activation"),
    recoverInterrupted: async ({ operationKey }) => completed.has(operationKey)
      ? { status: "completed", evidence: completed.get(operationKey) } : { status: "not-started" },
    recoverTarget: effect("forward-recover", ({ plan: selected }) => {
      assert.equal(selected.receipt.cutover.transferReceipt, "fixture-final-transfer");
      infrastructure.targetWriter = true; return result("forward");
    }),
    proveOriginalRestoration: async ({ plan: selected, gateContext }) => {
      events.push("prove-original"); infrastructure.targetWriter = false; infrastructure.legacyWriter = false; infrastructure.maintenance = false;
      return { evidenceId: "fixture-zero-effect-proof", transitionId: plan.transitionId, targetIdentity: plan.targetIdentity,
        targetOnlyEffects: infrastructure.targetEffects, targetFenced: true, legacyFenced: true,
        originalSourceUnchanged: true, originalBrowserWorkRecoverable: true,
        binding: cutoverReleaseBinding(selected, "before-original-restoration", gateContext) };
    },
  };
  return { directory, registrationPath, receiptPath, registrationBytes, receiptBytes, plan, state, effects, events, infrastructure, completed };
}

async function expectCode(promise, code) { await assert.rejects(promise, error => error.code === code); }
const restore = value => restoreOriginalPublication(value.plan, { state: value.state, effects: value.effects,
  request: { transitionId: value.plan.transitionId, restoreOriginal: true } });

test("transition uses the existing source lock and durably orders intent, final source, publication and activation", async context => {
  const f = fixture(context), phases = [];
  const save = f.state.saveJournal; f.state.saveJournal = journal => { phases.push(journal.phase); return save(journal); };
  const final = await executeCutoverTransition(f.plan, f);
  assert.equal(final.phase, "complete");
  assert.deepEqual(f.events, ["before-fence", "fence", "snapshot", "import", "quarantine", "target-backup", "finalize", "before-publication", "before-activation", "activate"]);
  assert.ok(phases.indexOf("intent-recorded") < phases.indexOf("fencing"));
  assert.ok(phases.indexOf("published") < phases.indexOf("activation-started"));
  assert.equal(f.infrastructure.legacyWriter, false); assert.equal(f.infrastructure.targetWriter, true);
  assert.equal(JSON.parse(readFileSync(f.registrationPath)).version, 2);
  assert.equal(f.state.readJournal().evidence.publication.durable, true);
});

test("another ordinary lifecycle lock blocks cutover before any journal or publication changes", async context => {
  const f = fixture(context), release = acquireTargetLock(f.directory);
  try { await expectCode(executeCutoverTransition(f.plan, f), "maintenance_active"); }
  finally { release(); }
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

test("missing preflight and wrong lock authority leave the original publication untouched", async context => {
  const f = fixture(context); f.effects.assertReleaseGates = async () => ({ status: "blocked" });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_release_blocked");
  assert.equal(f.state.readJournal(), null);
  f.state.lockIdentity = f.plan.targetIdentity;
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_lock_identity_mismatch");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

for (const action of ["fenceLegacy", "snapshotSource", "importHistory", "quarantineTarget", "backupTarget"]) {
  test(`interruption after ${action} effect resumes only after positive effect reconciliation`, async context => {
    const f = fixture(context), effect = f.effects[action]; let first = true;
    f.effects[action] = async args => { const result = await effect(args); if (first) { first = false; throw new Error("fixture interruption"); } return result; };
    await assert.rejects(executeCutoverTransition(f.plan, f), /fixture interruption/);
    assert.equal(f.infrastructure.targetWriter, false);
    f.effects.recoverInterrupted = undefined;
    await expectCode(executeCutoverTransition(f.plan, f), "cutover_effect_uncertain");
    // An actual adapter must retrieve its original idempotent operation receipt.
    // The fixture's retained evidence is the sole source of this positive fact.
    const field = { fenceLegacy: "fence", snapshotSource: "sourceSnapshot", importHistory: "imported", quarantineTarget: "quarantine", backupTarget: "targetBackup" }[action];
    const evidence = f.completed.get(`${f.plan.transitionId}:${f.state.readJournal().phase}`);
    f.effects.recoverInterrupted = async () => ({ status: "completed", evidence });
    const result = await executeCutoverTransition(f.plan, f);
    assert.equal(result.phase, "complete"); assert.equal(result.evidence[field].verified, true);
  });
}

test("import from a rehearsal snapshot cannot replace the final fenced source", async context => {
  const f = fixture(context); f.effects.importHistory = async () => ({ verified: true, evidenceId: "fixture-old-import", sourceStateId: "fixture-rehearsal", transferReceipt: {} });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_import_source_mismatch");
  assert.equal(f.state.readJournal().phase, "import-started"); assert.equal(f.infrastructure.targetWriter, false);
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

test("missing accepted transfer adapter blocks publication after safe preparation", async context => {
  const f = fixture(context); delete f.effects.finalizePublication;
  await expectCode(executeCutoverTransition(f.plan, f), "accepted-transfer-contract-pending");
  assert.equal(f.state.readJournal().phase, "target-verified");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

test("finalization cannot substitute different selected artifacts or target identities", async context => {
  const f = fixture(context), finalize = f.effects.finalizePublication;
  f.effects.finalizePublication = async args => { const result = await finalize(args); result.registration.cutover.images.web = "sha256:unselected"; return result; };
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_final_identity_changed");
  assert.equal(f.infrastructure.targetWriter, false);
});

test("half-published registration/receipt blocks startup until explicit verified original restoration", async context => {
  const f = fixture(context); f.state.writeReceipt = () => { throw new Error("fixture receipt interruption"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture receipt interruption/);
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_publication_mismatch");
  assert.equal(f.infrastructure.legacyWriter, false); assert.equal(f.infrastructure.targetWriter, false);
  const realState = createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath, receiptPath: f.receiptPath, lockIdentity: f.plan.lockIdentity });
  f.state = realState;
  assert.equal((await restore(f)).phase, "original-restored");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes); assert.deepEqual(readFileSync(f.receiptPath), f.receiptBytes);
});

test("fully persisted pair before published journal write is reconciled with fresh durability confirmation", async context => {
  const f = fixture(context), save = f.state.saveJournal; let once = true;
  f.state.saveJournal = journal => { if (journal.phase === "published" && once) { once = false; throw new Error("fixture journal interruption"); } return save(journal); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture journal interruption/);
  assert.equal(f.state.readJournal().phase, "receipt-publication-started");
  assert.equal((await executeCutoverTransition(f.plan, f)).phase, "complete");
});

test("uncertain activation allows explicit forward recovery and never automatic legacy fallback", async context => {
  const f = fixture(context), activate = f.effects.activateTarget;
  f.effects.activateTarget = async args => { await activate(args); f.infrastructure.targetEffects = "uncertain"; throw new Error("fixture activation interruption"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture activation interruption/);
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_forward_recovery_required");
  await expectCode(restore(f), "cutover_restoration_unproven");
  assert.equal(f.state.readJournal().phase, "activation-started");
  f.infrastructure.targetWriter = false; // Explicit fixture-only quarantine before forward repair.
  assert.equal((await executeCutoverTransition(f.plan, { ...f, recovery: "forward" })).phase, "complete");
  assert.equal(f.infrastructure.legacyWriter, false);
});

test("confirmed target effects prohibit restoration and preserve the candidate publication", async context => {
  const f = fixture(context); await executeCutoverTransition(f.plan, f); f.infrastructure.targetEffects = "accepted";
  const candidate = readFileSync(f.registrationPath);
  await expectCode(restore(f), "cutover_restoration_unproven");
  assert.deepEqual(readFileSync(f.registrationPath), candidate); assert.equal(f.state.readJournal().phase, "complete");
});

test("explicit zero-effect restoration preserves exact original bytes and original receipt absence", async context => {
  const f = fixture(context, { absentReceipt: true }); await executeCutoverTransition(f.plan, f);
  await expectCode(restoreOriginalPublication(f.plan, { state: f.state, effects: f.effects }), "cutover_restoration_not_requested");
  await restore(f);
  assert.deepEqual(f.state.readPublication(), { registration: f.registrationBytes, receipt: null });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_restoration_pending");
});

test("changed pinned plan cannot overwrite original interruption evidence", async context => {
  const f = fixture(context); f.effects.snapshotSource = async () => { throw new Error("fixture interrupted"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture interrupted/);
  const original = readFileSync(join(f.directory, "cutover-transition.json"));
  await expectCode(executeCutoverTransition({ ...f.plan, revision: "different" }, f), "cutover_journal_mismatch");
  assert.deepEqual(readFileSync(join(f.directory, "cutover-transition.json")), original);
});

test("a ready verdict for another plan, checkpoint or previous lock invocation cannot authorize work", async context => {
  const f = fixture(context), realGate = f.effects.assertReleaseGates;
  for (const change of [binding => { binding.planSha256 = "different-plan"; },
    binding => { binding.checkpoint = "before-activation"; }, binding => { binding.gateContext = "old-lock-invocation"; }]) {
    f.effects.assertReleaseGates = async (...args) => { const result = await realGate(...args); change(result.binding); return result; };
    await expectCode(executeCutoverTransition(f.plan, f), "cutover_release_blocked");
    assert.equal(f.state.readJournal(), null);
    assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
  }
});

test("fresh resume fence and original browser recoverability are required before effect recovery", async context => {
  const f = fixture(context); f.effects.snapshotSource = async () => { throw new Error("fixture snapshot interrupted"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture snapshot interrupted/);
  let recovered = false; f.effects.recoverInterrupted = async () => { recovered = true; return { status: "not-started" }; };
  const observe = f.effects.verifyResumptionFence;
  f.effects.verifyResumptionFence = async args => ({ ...await observe(args), originalBrowserWorkRecoverable: false });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_resume_fence_unverified");
  assert.equal(recovered, false); assert.equal(f.state.readJournal().phase, "snapshot-started");
});

test("corrupted original byte snapshots cannot be resumed or restored", async context => {
  const f = fixture(context); f.effects.snapshotSource = async () => { throw new Error("fixture interrupted"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture interrupted/);
  const journal = f.state.readJournal(); journal.original.registration = Buffer.from('{"version":1,"project":"wrong-source"}').toString("base64");
  f.state.saveJournal(journal);
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_original_corrupt");
  await expectCode(restore(f), "cutover_original_corrupt");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

test("matching records with unconfirmed durability cannot cross the activation boundary", async context => {
  const f = fixture(context); f.state.confirmPublicationDurability = () => ({ durable: false, evidenceId: "fixture-fsync-failed" });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_publication_not_durable");
  assert.equal(f.infrastructure.targetWriter, false); assert.equal(f.state.readJournal().phase, "receipt-publication-started");
  assert.ok(!f.events.includes("activate"));
});

test("interrupted original restoration remains fenced and explicitly retries the same exact prior pair", async context => {
  const f = fixture(context); await executeCutoverTransition(f.plan, f);
  const writeReceipt = f.state.writeReceipt; let once = true;
  f.state.writeReceipt = bytes => { if (once) { once = false; throw new Error("fixture restore receipt interruption"); } return writeReceipt(bytes); };
  await assert.rejects(restore(f), /fixture restore receipt interruption/);
  assert.equal(f.state.readJournal().phase, "restoration-started"); assert.equal(f.infrastructure.targetWriter, false);
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_restoration_pending");
  assert.equal((await restore(f)).phase, "original-restored");
  assert.deepEqual(f.state.readPublication(), { registration: f.registrationBytes, receipt: f.receiptBytes });
});

test("production execution is blocked even when a caller supplies fixture-shaped ready evidence", async context => {
  const f = fixture(context); f.effects.executionScope = "production";
  await expectCode(executeCutoverTransition(f.plan, f), "accepted-transfer-contract-pending");
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});

test("final publication cannot replace the transition authority through extra planner-result fields", async context => {
  const f = fixture(context), finalize = f.effects.finalizePublication;
  f.effects.finalizePublication = async args => ({ ...await finalize(args), targetIdentity: "different-target-authority" });
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_final_identity_changed");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes);
});

test("state paths cannot alias the journal, original lock or each other's publication record", context => {
  const f = fixture(context);
  for (const receiptPath of [f.registrationPath, join(f.directory, "maintenance.lock"), join(f.directory, "cutover-transition.json")])
    assert.throws(() => createCutoverState({ stateDirectory: f.directory, registrationPath: f.registrationPath,
      receiptPath, lockIdentity: f.plan.lockIdentity }), error => error.code === "cutover_state_paths_overlap");
  assert.equal(f.state.readJournal(), null);
});

test("a journal claiming completed preparation cannot skip its verified stage evidence", async context => {
  const f = fixture(context); f.effects.snapshotSource = async () => { throw new Error("fixture interrupted"); };
  await assert.rejects(executeCutoverTransition(f.plan, f), /fixture interrupted/);
  const journal = f.state.readJournal(); journal.phase = "target-verified"; f.state.saveJournal(journal);
  await expectCode(executeCutoverTransition(f.plan, f), "cutover_effect_unverified");
  assert.deepEqual(readFileSync(f.registrationPath), f.registrationBytes); assert.equal(f.infrastructure.targetWriter, false);
});
