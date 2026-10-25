import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { acquireTargetLock } from "../tempo-deployment.mjs";
import { createCutoverState, cutoverReleaseBinding, executeFreshStartTransition } from "../tempo-cutover-transition.mjs";

// Synthetic semantic capabilities only; no installed graph/schema mapping,
// public contract acceptance, real source observation or cutover permission.
function fixture(context) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-fresh-graph-fixture-"));
  context.after(() => rmSync(directory, { recursive: true, force: true }));
  const registrationPath = join(directory, "registration.json"), receiptPath = join(directory, "deployment.json");
  const originalBytes = { registration: Buffer.from(' {"version":1,"project":"fixture-original"}\r\n'), receipt: Buffer.from('{"version":1,"revision":"fixture-old"}\n\n') };
  for (const [name, bytes] of Object.entries(originalBytes)) writeFileSync(name === "registration" ? registrationPath : receiptPath, bytes, { mode: 0o600 });
  const graph = { mode: "graph-preserving-fresh-start", transitionId: "fixture-fresh", phase: "quarantined",
    revision: "fixture-revision", images: { "study-web": "fixture-web", learning: "fixture-learning", postgres: "fixture-postgres" },
    workspace: "fixture-new-workspace", ingress: { origin: "https://fresh.fixture.invalid", hosts: ["fresh.fixture.invalid"] },
    database: { name: "fixture-clean", volume: "fixture-new-volume" }, schema: 1,
    composeFiles: ["fixture-study-compose.yml"], volumes: { data: { name: "fixture-new-volume", external: true } },
    graphSourceReference: "fixture-complete-authored-graph", graphReceipt: null, freshInitReceipt: null };
  const plan = { version: 1, transitionId: graph.transitionId, revision: graph.revision,
    sourceIdentity: "fixture-source", targetIdentity: "fixture-clean-target", lockIdentity: "fixture-source", freshStartChoice: "graph-preserving-fresh-start",
    registration: { version: 2, project: "fixture-new", cutover: structuredClone(graph) },
    receipt: { version: 2, revision: graph.revision, cutover: structuredClone(graph) },
    original: { registration: JSON.parse(originalBytes.registration), receipt: JSON.parse(originalBytes.receipt) },
    graphSource: { fixtureOnly: true, reference: graph.graphSourceReference }, graphVerification: null, freshInitialization: null };
  const state = createCutoverState({ stateDirectory: directory, registrationPath, receiptPath, lockIdentity: plan.lockIdentity });
  const events = [], completed = new Map();
  const infra = { originalWriter: true, targetWriter: false, acceptedTargetEffects: 0 };
  const fact = (name, extra = {}) => ({ verified: true, evidenceId: `fixture-${name}`, ...extra });
  const target = { targetIdentity: plan.targetIdentity, workspace: graph.workspace, ingressOrigin: graph.ingress.origin, schema: graph.schema };
  const source = { graphSourceReference: graph.graphSourceReference };
  const effect = (name, run) => async args => {
    events.push(name); assert.ok(state.readJournal());
    const result = run(args); completed.set(args.operationKey, structuredClone(result)); return result;
  };
  const effects = {
    executionScope: "synthetic-fixture",
    assertReleaseGates: async (selected, checkpoint, facts, gateContext) => {
      events.push(checkpoint);
      if (checkpoint === "before-authority-switch") {
        assert.equal(facts.quarantine.graphClosureVerified, true); assert.equal(facts.graphRecoveryCopy.fullSourcePreserved, true);
        if (state.readJournal().phase === "target-verified") assert.equal(infra.originalWriter, true); // No fencing until specific confirmation.
      }
      if (["before-admission", "before-forward-recovery"].includes(checkpoint)) {
        assert.equal(facts.publication.durable, true); assert.equal(selected.receipt.cutover.graphReceipt, "fixture-graph-receipt");
        assert.equal(selected.receipt.cutover.freshInitReceipt, "fixture-fresh-init-receipt");
      }
      return { status: "ready", evidenceId: `fixture-confirmation-${checkpoint}`, binding: cutoverReleaseBinding(selected, checkpoint, gateContext) };
    },
    verifyGraphSource: effect("verify-source-closure", () => fact("source", { ...source, closureVerified: true })),
    createVerifiedGraphCopy: effect("copy-full-graph", () => fact("copy", { ...source, restoreVerified: true, immutable: true, fullSourcePreserved: true })),
    provisionCleanTarget: effect("provision-clean", () => fact("provision", { ...target, clean: true, independent: true })),
    importGraph: effect("public-graph-import", () => fact("import", { ...source, ...target, graphReceipt: { fixtureOnly: true, reference: "fixture-graph-receipt" } })),
    initializeFreshLearning: effect("public-fresh-init", () => fact("fresh-init", { ...target, freshInitReceipt: { fixtureOnly: true, reference: "fixture-fresh-init-receipt" } })),
    verifyGraphTarget: effect("verify-graph-quarantine", () => fact("quarantine", { ...target, ...source,
      graphClosureVerified: true, readOnly: true, maintenanceDisabled: true, mutatingTrafficDisabled: true })),
    finalizeFreshStartPublication: async ({ plan: selected, facts }) => {
      events.push("finalize-fresh-pair"); const registration = structuredClone(selected.registration), receipt = structuredClone(selected.receipt);
      for (const value of [registration, receipt]) {
        value.cutover.graphReceipt = facts.graphImported.graphReceipt.reference;
        value.cutover.freshInitReceipt = facts.freshInitialization.freshInitReceipt.reference;
      }
      return { registration, receipt, graphVerification: { fixtureOnly: true, verified: true }, freshInitialization: { fixtureOnly: true, isolated: true } };
    },
    fenceOriginalAuthority: effect("fence-original", () => { infra.originalWriter = false; return fact("fence", { originalFenced: true, targetQuarantined: true }); }),
    admitTarget: async ({ plan: selected }) => {
      events.push("admit"); assert.equal(state.readJournal().phase, "activation-started"); assert.equal(infra.originalWriter, false);
      assert.equal(selected.receipt.cutover.freshInitReceipt, "fixture-fresh-init-receipt"); infra.targetWriter = true;
    },
    verifyAdmission: async () => fact("admission"),
    recoverInterrupted: async ({ operationKey }) => completed.has(operationKey)
      ? { status: "completed", evidence: completed.get(operationKey) } : { status: "not-started" },
    observeResumption: async ({ gateContext }) => fact("resumption", { gateContext, targetQuarantined: !infra.targetWriter, originalWriterStateConfirmed: true, originalFenced: !infra.originalWriter }),
    recoverTarget: async ({ plan: selected }) => {
      events.push("forward-repair"); assert.equal(selected.receipt.cutover.graphReceipt, "fixture-graph-receipt");
      infra.targetWriter = true; return fact("forward-admission");
    },
  };
  return { directory, registrationPath, receiptPath, originalBytes, plan, state, effects, events, infra, completed };
}
const execute = f => executeFreshStartTransition(f.plan, f);
async function code(promise, expected) { await assert.rejects(promise, error => error.code === expected); }

test("fresh start verifies a full graph recovery copy and clean graph target before cutover confirmation and separate admission", async context => {
  const f = fixture(context); const result = await execute(f);
  assert.equal(result.phase, "complete");
  assert.deepEqual(f.events, ["before-preparation", "verify-source-closure", "copy-full-graph", "provision-clean", "public-graph-import", "public-fresh-init",
    "verify-graph-quarantine", "finalize-fresh-pair", "before-authority-switch", "fence-original", "before-admission", "admit"]);
  assert.equal(f.infra.originalWriter, false); assert.equal(f.infra.targetWriter, true);
  assert.equal(result.evidence.publication.durable, true);
  assert.ok(!Object.hasOwn(result.evidence, "historicalTransfer"));
});

test("the existing original lock blocks fresh start before any graph effect", async context => {
  const f = fixture(context), release = acquireTargetLock(f.directory);
  try { await code(execute(f), "maintenance_active"); } finally { release(); }
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});

for (const [port, blocker] of [["verifyGraphSource", "graph-contract-pending"], ["initializeFreshLearning", "fresh-initialization-contract-pending"]])
  test(`missing ${port} public contract reports its exact blocker before mutation`, async context => {
    const f = fixture(context); delete f.effects[port]; await code(execute(f), blocker);
    assert.equal(f.state.readJournal(), null); assert.deepEqual(f.state.readPublication(), f.originalBytes);
  });

for (const [port, field, value, blocker] of [
  ["verifyGraphSource", "closureVerified", false, "fresh_start_evidence_missing"],
  ["createVerifiedGraphCopy", "fullSourcePreserved", false, "fresh_start_evidence_missing"],
  ["provisionCleanTarget", "clean", false, "fresh_start_evidence_missing"],
  ["provisionCleanTarget", "independent", false, "fresh_start_evidence_missing"],
  ["provisionCleanTarget", "schema", 2, "fresh_start_schema_mismatch"],
  ["importGraph", "graphSourceReference", "wrong-graph", "fresh_start_graph_mismatch"],
  ["initializeFreshLearning", "ingressOrigin", "https://old.fixture.invalid", "fresh_start_authority_mismatch"],
  ["verifyGraphTarget", "mutatingTrafficDisabled", false, "fresh_start_evidence_missing"],
  ["verifyGraphTarget", "schema", 2, "fresh_start_schema_mismatch"],
]) test(`${port} cannot advance with invalid ${field}`, async context => {
  const f = fixture(context), prior = f.effects[port]; f.effects[port] = async args => ({ ...await prior(args), [field]: value });
  await code(execute(f), blocker); assert.equal(f.infra.originalWriter, true); assert.equal(f.infra.targetWriter, false);
  assert.deepEqual(f.state.readPublication(), f.originalBytes);
});

test("unconfirmed cutover leaves original authority and registration untouched after graph preparation", async context => {
  const f = fixture(context), gate = f.effects.assertReleaseGates;
  f.effects.assertReleaseGates = async (...args) => args[1] === "before-authority-switch" ? { status: "blocked" } : gate(...args);
  await code(execute(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal().phase, "target-verified");
  assert.equal(f.infra.originalWriter, true); assert.deepEqual(f.state.readPublication(), f.originalBytes);
});

test("separate admission confirmation is required after durable fresh-start publication", async context => {
  const f = fixture(context), gate = f.effects.assertReleaseGates;
  f.effects.assertReleaseGates = async (...args) => args[1] === "before-admission" ? { status: "blocked" } : gate(...args);
  await code(execute(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal().phase, "published");
  assert.equal(f.infra.originalWriter, false); assert.equal(f.infra.targetWriter, false); assert.ok(!f.events.includes("admit"));
});

for (const port of ["verifyGraphSource", "createVerifiedGraphCopy", "provisionCleanTarget", "importGraph", "initializeFreshLearning", "verifyGraphTarget", "fenceOriginalAuthority"])
  test(`interrupted ${port} requires positive reconciliation of this transition's operation`, async context => {
    const f = fixture(context), prior = f.effects[port]; let once = true;
    f.effects[port] = async args => { const fact = await prior(args); if (once) { once = false; throw new Error("fixture interruption"); } return fact; };
    await assert.rejects(execute(f), /fixture interruption/); f.effects.recoverInterrupted = undefined;
    await code(execute(f), "fresh_start_effect_uncertain");
    f.effects.recoverInterrupted = async ({ operationKey }) => ({ status: "completed", evidence: f.completed.get(operationKey) });
    // At the already-completed fence edge the specific approval was obtained;
    // the fixture gate only observes that fact rather than reopening original.
    if (!f.infra.originalWriter) {
      const gate = f.effects.assertReleaseGates;
      f.effects.assertReleaseGates = async (...args) => args[1] === "before-authority-switch"
        ? { status: "ready", evidenceId: "fixture-reconfirmed-cutover", binding: cutoverReleaseBinding(args[0], args[1], args[3]) } : gate(...args);
    }
    assert.equal((await execute(f)).phase, "complete");
  });

test("a split fresh-start publication blocks admission without automatic old-authority restoration", async context => {
  const f = fixture(context); f.state.writeReceipt = () => { throw new Error("fixture split publication"); };
  await assert.rejects(execute(f), /fixture split publication/); await code(execute(f), "fresh_start_publication_mismatch");
  assert.equal(f.infra.originalWriter, false); assert.equal(f.infra.targetWriter, false);
  assert.equal(JSON.parse(readFileSync(f.registrationPath)).version, 2);
});

test("new accepted target effects survive explicit forward repair after uncertain admission", async context => {
  const f = fixture(context), admit = f.effects.admitTarget;
  f.effects.admitTarget = async args => { await admit(args); f.infra.acceptedTargetEffects = 3; throw new Error("fixture uncertain admission"); };
  await assert.rejects(execute(f), /fixture uncertain admission/); await code(execute(f), "fresh_start_forward_recovery_required");
  f.infra.targetWriter = false; // Explicit fixture quarantine; no reset/restore.
  const final = await executeFreshStartTransition(f.plan, { ...f, recovery: "forward" });
  assert.equal(final.phase, "complete"); assert.equal(f.infra.acceptedTargetEffects, 3); assert.equal(f.infra.originalWriter, false);
});

test("publication finalization cannot substitute graph source, artifacts or target identity", async context => {
  const f = fixture(context), finalize = f.effects.finalizeFreshStartPublication;
  f.effects.finalizeFreshStartPublication = async args => { const result = await finalize(args); result.registration.cutover.graphSourceReference = "unselected"; return result; };
  await code(execute(f), "fresh_start_identity_changed"); assert.equal(f.infra.originalWriter, true);
});

test("ready evidence from a different lock invocation cannot begin fresh-start preparation", async context => {
  const f = fixture(context), gate = f.effects.assertReleaseGates;
  f.effects.assertReleaseGates = async (...args) => { const result = await gate(...args); result.binding.gateContext = "old-invocation"; return result; };
  await code(execute(f), "fresh_start_gate_blocked"); assert.equal(f.state.readJournal(), null);
});

test("old publication corruption and stale resumption observations cannot authorize fresh effects", async context => {
  const f = fixture(context); f.effects.importGraph = async () => { throw new Error("fixture interrupted"); };
  await assert.rejects(execute(f), /fixture interrupted/);
  f.effects.observeResumption = async () => ({ verified: true, evidenceId: "fixture-stale", gateContext: "stale", targetQuarantined: true });
  await code(execute(f), "fresh_start_resume_unverified");
  const journal = f.state.readJournal(); journal.original.registration = Buffer.from('{}').toString("base64"); f.state.saveJournal(journal);
  await code(execute(f), "fresh_start_original_corrupt");
});

test("production graph/cutover execution remains blocked and original lock identity stays explicit", async context => {
  const f = fixture(context); f.effects.executionScope = "production"; await code(execute(f), "graph-contract-pending");
  f.effects.executionScope = "synthetic-fixture"; f.state.lockIdentity = f.plan.targetIdentity; await code(execute(f), "fresh_start_lock_mismatch");
  assert.equal(f.state.readJournal(), null); assert.deepEqual(f.state.readPublication(), f.originalBytes);
});

test("known absent prior deployment receipt is allowed without an old verified deployment prerequisite", async context => {
  const f = fixture(context); unlinkSync(f.receiptPath); f.plan.original.receipt = null;
  const result = await execute(f); assert.equal(result.phase, "complete"); assert.equal(result.original.receipt, null);
});

test("unknown prior publication and absent explicit fresh-start choice block without graph effects", async context => {
  const f = fixture(context); delete f.plan.original.receipt;
  await code(execute(f), "fresh_start_prior_publication_unknown");
  f.plan.original.receipt = JSON.parse(f.originalBytes.receipt); delete f.plan.freshStartChoice;
  await code(execute(f), "graph-contract-pending"); assert.equal(f.state.readJournal(), null); assert.deepEqual(f.events, []);
});

test("new graph publication cannot open traffic without confirmed file and directory durability", async context => {
  const f = fixture(context); f.state.confirmPublicationDurability = () => ({ durable: false, evidenceId: "fixture-failed-fsync" });
  await code(execute(f), "fresh_start_publication_not_durable");
  assert.equal(f.state.readJournal().phase, "receipt-publication-started"); assert.equal(f.infra.targetWriter, false);
});

test("finalization cannot replace an already pinned public graph receipt", async context => {
  const f = fixture(context);
  f.plan.registration.cutover.graphReceipt = "previously-pinned-graph"; f.plan.receipt.cutover.graphReceipt = "previously-pinned-graph";
  await code(execute(f), "fresh_start_identity_changed"); assert.equal(f.infra.originalWriter, true);
});
