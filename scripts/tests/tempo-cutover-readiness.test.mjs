import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { evaluateFreshStartReadiness, readFreshStartReadinessFixture, readinessBinding } from "../tempo-cutover-readiness.mjs";

const serialized = readFileSync(new URL("./fixtures/tempo-cutover-readiness/synthetic-fresh-start.json", import.meta.url), "utf8");
const graphAdapter = { scope: "synthetic-fixture", validate(input, binding) {
  const exactSource = input.graphSource?.title === "Synthetic authored opening"
    && input.graphSource?.repertoires?.[0]?.title === "Synthetic repertoire name"
    && input.graphVerification?.sourceRef === binding.graphSourceReference;
  const exactImport = !binding.graphReceipt || (input.graphImported?.graphReceipt?.id === binding.graphReceipt
    && input.graphRecoveryCopy?.graphSourceReference === binding.graphSourceReference);
  return { status: exactSource && exactImport ? "verified" : "rejected", binding };
} };
const freshInitializationAdapter = { scope: "synthetic-fixture", validate(input, binding) {
  return { status: !binding.freshInitReceipt || (input.freshInitialization?.receiptRef === binding.freshInitReceipt
    && input.verification?.freshInitReceipt?.id === binding.freshInitReceipt
    && input.freshInitialization?.workspace === binding.workspace
    && input.freshInitialization?.ingressOrigin === binding.ingressOrigin) ? "verified" : "rejected", binding };
} };
function fixture(checkpoint = "before-preparation") {
  const input = readFreshStartReadinessFixture(serialized);
  input.checkpoint = checkpoint;
  input.evidence.binding = readinessBinding(input.plan, checkpoint, input.gateContext);
  return input;
}
const evaluate = input => evaluateFreshStartReadiness(input, { graphAdapter, freshInitializationAdapter });
const codes = result => result.blockers.map(x => x.code);

test("pure preparation needs no cutover confirmation, prior receipt or future target effects", () => {
  const input = fixture();
  delete input.facts; delete input.evidence.cutoverConfirmation; delete input.evidence.authorityIsolation;
  assert.equal(input.plan.original.receipt, null);
  const result = evaluate(input);
  assert.equal(result.status, "ready");
  assert.equal(result.liveExecutionAllowed, false);
  assert.equal(result.production.status, "blocked");
  assert.deepEqual(result.production.blockers.map(x => x.code), ["graph-contract-pending", "fresh-initialization-contract-pending"]);
});

test("exact graph-preserving fresh-switch and admission fixtures remain production blocked", () => {
  for (const checkpoint of ["before-authority-switch", "before-admission", "before-forward-recovery"]) {
    const input = fixture(checkpoint), before = structuredClone(input);
    assert.equal(evaluate(input).status, "ready");
    assert.equal(evaluate(input).production.status, "blocked");
    assert.deepEqual(input, before);
  }
});

test("missing public graph and fresh-init adapters are explicit provider blockers", () => {
  assert.deepEqual(codes(evaluateFreshStartReadiness(fixture("before-authority-switch"))),
    ["graph-contract-pending", "fresh-initialization-contract-pending"]);
});

test("actual provider scope is never inferred from nonempty public receipts", () => {
  const input = fixture("before-admission");
  const result = evaluateFreshStartReadiness(input, { graphAdapter: { ...graphAdapter, scope: "actual" }, freshInitializationAdapter });
  assert.ok(codes(result).includes("graph-contract-pending"));
});

test("old history, schedules, background jobs and pending envelope receipts are absent from fresh gates", () => {
  const input = fixture("before-authority-switch");
  assert.equal(evaluate(input).status, "ready");
  assert.equal(Object.hasOwn(input.plan, "transferAcceptance"), false);
  assert.equal(Object.hasOwn(input.facts, "sourceSnapshot"), false);
  assert.equal(Object.hasOwn(input.facts, "targetBackup"), false);
});

test("current nonce and finalized graph reference bind readiness to the exact invocation", () => {
  const input = fixture("before-admission");
  input.gateContext = "synthetic-next-lock-invocation";
  assert.ok(codes(evaluate(input)).includes("readiness-binding-mismatch"));
  const finalized = fixture("before-authority-switch");
  finalized.plan.receipt.cutover.graphReceipt = "another-receipt";
  assert.ok(codes(evaluate(finalized)).includes("readiness-binding-mismatch"));
});

test("canonical binding preserves array order and rejects invalid JSON or changed lock authority", () => {
  const input = fixture();
  const reordered = Object.fromEntries(Object.entries(input.plan).reverse());
  assert.deepEqual(readinessBinding(reordered, input.checkpoint, input.gateContext), input.evidence.binding);
  input.plan.lockIdentity = input.plan.targetIdentity;
  assert.ok(codes(evaluate(input)).includes("invalid-fresh-start-plan"));
  input.plan.lockIdentity = input.plan.sourceIdentity; input.plan.unsupported = new Date();
  assert.ok(codes(evaluate(input)).includes("invalid-fresh-start-plan"));
});

for (const [fact, key, value, blocker] of [
  ["graphSource", "closureVerified", false, "graph-source-unverified"],
  ["graphRecoveryCopy", "graphSourceReference", "other-source", "graph-recovery-copy-unverified"],
  ["graphRecoveryCopy", "restoreVerified", false, "graph-recovery-copy-unverified"],
  ["graphRecoveryCopy", "immutable", false, "graph-recovery-copy-unverified"],
  ["graphRecoveryCopy", "fullSourcePreserved", false, "graph-recovery-copy-unverified"],
  ["targetProvisioning", "clean", false, "clean-target-unverified"],
  ["targetProvisioning", "independent", false, "clean-target-unverified"],
  ["targetProvisioning", "targetIdentity", "live-target", "clean-target-unverified"],
  ["targetProvisioning", "schema", 999, "clean-target-unverified"],
  ["graphImported", "graphSourceReference", "other-source", "graph-import-unverified"],
  ["freshInitialization", "workspace", "old-workspace", "fresh-initialization-unverified"],
  ["quarantine", "graphClosureVerified", false, "target-quarantine-unverified"],
  ["quarantine", "schema", 999, "target-quarantine-unverified"],
  ["quarantine", "readOnly", false, "target-quarantine-unverified"],
  ["quarantine", "maintenanceDisabled", false, "target-quarantine-unverified"],
  ["quarantine", "mutatingTrafficDisabled", false, "target-quarantine-unverified"],
]) test(`fresh switch rejects ${fact}.${key} mismatch`, () => {
  const input = fixture("before-authority-switch"); input.facts[fact][key] = value;
  assert.ok(codes(evaluate(input)).includes(blocker));
});

test("reduced graph data missing authored repertoire names cannot qualify full source closure", () => {
  const input = fixture("before-authority-switch"); delete input.plan.graphSource.repertoires[0].title;
  input.evidence.binding = readinessBinding(input.plan, input.checkpoint, input.gateContext);
  assert.ok(codes(evaluate(input)).includes("graph-verification-rejected"));
});

test("specific cutover confirmation follows safe preparation and binds the selected authority", () => {
  const input = fixture("before-authority-switch");
  delete input.evidence.cutoverConfirmation;
  assert.ok(codes(evaluate(input)).includes("exact-cutover-confirmation-missing"));
  const foreign = fixture("before-authority-switch"); foreign.evidence.cutoverConfirmation.workspace = "other";
  assert.ok(codes(evaluate(foreign)).includes("exact-cutover-confirmation-missing"));
});

test("old cached authority isolation is required without draining old pending work", () => {
  const input = fixture("before-authority-switch"); input.evidence.authorityIsolation.oldReplayDisabled = false;
  assert.ok(codes(evaluate(input)).includes("new-authority-isolation-unverified"));
  input.evidence.authorityIsolation.oldReplayDisabled = true;
  input.evidence.authorityIsolation.ingressOrigin = "https://another.invalid";
  assert.ok(codes(evaluate(input)).includes("new-authority-isolation-unverified"));
});

test("admission requires both durable matching publication and separate confirmation", () => {
  const input = fixture("before-admission"); input.facts.publication.durable = false;
  assert.ok(codes(evaluate(input)).includes("publication-unverified"));
  delete input.evidence.targetAdmission;
  assert.ok(codes(evaluate(input)).includes("target-admission-unconfirmed"));
});

test("resumption needs freshly reobserved phase-appropriate writer state and quarantine", () => {
  const input = fixture("before-resume");
  assert.equal(evaluate(input).status, "ready");
  input.facts.resumption.gateContext = "previous-invocation";
  assert.ok(codes(evaluate(input)).includes("resumption-fence-unverified"));
});

test("forward repair cannot discard newly accepted fresh-target effects", () => {
  const input = fixture("before-forward-recovery"); input.evidence.forwardRecovery.restoresOlderSnapshot = true;
  assert.ok(codes(evaluate(input)).includes("forward-recovery-unverified"));
});

test("unbound, throwing or mutating fixture plugins fail independently and preserve inputs", () => {
  for (const validate of [() => ({ ready: true }), () => ({ status: "verified" }),
    async () => ({ status: "verified" }),
    () => { throw new Error("private contents"); }, input => { input.graphSource.title = "changed"; }]) {
    const input = fixture(), before = structuredClone(input);
    const result = evaluateFreshStartReadiness(input, { graphAdapter: { scope: "synthetic-fixture", validate } });
    assert.ok(codes(result).includes("graph-verification-rejected")); assert.deepEqual(input, before);
    assert.doesNotMatch(JSON.stringify(result), /private contents/);
  }
});

test("fixture reader and evaluator reject unlabelled/live input without live effects", () => {
  assert.throws(() => readFreshStartReadinessFixture("not JSON"), /valid JSON/);
  assert.throws(() => readFreshStartReadinessFixture("x".repeat(1024*1024+1)), /1 MiB/);
  const input = fixture(); input.evidence.scope = "actual";
  assert.throws(() => readFreshStartReadinessFixture(JSON.stringify(input)), /synthetic-fixture/);
  assert.ok(codes(evaluate(input)).includes("live-fresh-start-unavailable"));
});
