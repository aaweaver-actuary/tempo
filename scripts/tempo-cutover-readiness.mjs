// Pure graph-preserving fresh-start preparation. No live launcher or data I/O.
import { createHash } from "node:crypto";

export const freshStartCheckpoints = Object.freeze([
  "before-preparation", "before-authority-switch", "before-admission",
  "before-resume", "before-forward-recovery",
]);
const text = value => typeof value === "string" && value.trim().length > 0;
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const pending = Object.freeze([
  Object.freeze({ code: "graph-contract-pending", message: "Actual authored graph/source closure, recovery-copy and import verification are not accepted." }),
  Object.freeze({ code: "fresh-initialization-contract-pending", message: "Actual clean-target initialization and old-authority isolation proof are not accepted." }),
]);

function canonicalJson(value, seen = new Set()) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if ((!record(value) && !Array.isArray(value)) || (!Array.isArray(value)
    && ![Object.prototype, null].includes(Object.getPrototypeOf(value)))) throw new Error("Plan must contain plain JSON values.");
  if (seen.has(value)) throw new Error("Plan must not contain cycles.");
  seen.add(value);
  const keys = Array.isArray(value) ? Array.from({ length: value.length }, (_, index) => String(index)) : Object.keys(value).sort();
  const values = keys.map(key => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !Object.hasOwn(descriptor, "value")) throw new Error("Plan must not contain sparse arrays or accessors.");
    return canonicalJson(descriptor.value, seen);
  });
  const result = Array.isArray(value) ? `[${values.join(",")}]`
    : `{${keys.map((key, index) => `${JSON.stringify(key)}:${values[index]}`).join(",")}}`;
  seen.delete(value);
  return result;
}
const sameJson = (left, right) => { try { return canonicalJson(left) === canonicalJson(right); } catch { return false; } };
function frozenCopy(value) {
  const copy = structuredClone(value), seen = new Set();
  const freeze = entry => {
    if (entry !== null && typeof entry === "object" && !seen.has(entry)) {
      seen.add(entry); Object.values(entry).forEach(freeze); Object.freeze(entry);
    }
    return entry;
  };
  return freeze(copy);
}

/** Public fixture gate protocol: complete canonical JSON plan + checkpoint + current lock nonce. */
export function readinessBinding(plan, checkpoint, gateContext) {
  if (!freshStartCheckpoints.includes(checkpoint) || !text(gateContext)) throw new Error("A known checkpoint and current lock context are required.");
  if (!record(plan) || plan.version !== 1 || !text(plan.transitionId) || !text(plan.revision)
    || !text(plan.sourceIdentity) || !text(plan.targetIdentity) || plan.lockIdentity !== plan.sourceIdentity)
    throw new Error("A pinned fresh-start plan retaining original lock authority is required.");
  return Object.freeze({ version: 1, checkpoint, gateContext,
    planSha256: createHash("sha256").update(canonicalJson(plan)).digest("hex") });
}

/** Parse a bounded labelled fixture, not an actual graph export, capture or receipt. */
export function readFreshStartReadinessFixture(serialized) {
  if (typeof serialized !== "string" || Buffer.byteLength(serialized) > 1024 * 1024) throw new Error("Fixture must be JSON text of at most 1 MiB.");
  let input;
  try { input = JSON.parse(serialized); } catch { throw new Error("Fixture is not valid JSON."); }
  if (!record(input) || input.evidence?.scope !== "synthetic-fixture") throw new Error("Fixture must declare synthetic-fixture scope.");
  return input;
}

/**
 * Public graph and fresh-init receipts stay opaque. Inject only fixture ports
 * {scope:'synthetic-fixture',validate(input,expected)} -> {status:'verified',binding}.
 * Actual owner CLI/HTTP adapters and acceptance remain missing. A positive fixture
 * result never grants production execution or rewrites an old browser authority.
 */
export function evaluateFreshStartReadiness({ plan, checkpoint, gateContext, evidence, facts = {} } = {},
  { graphAdapter, freshInitializationAdapter } = {}) {
  return evaluateReadiness({ plan, checkpoint, gateContext, evidence, facts },
    { graphAdapter, freshInitializationAdapter }, "synthetic-fixture");
}

/** Actual provider protocol, still pure and never an authorization executor.
 * The composition supplies independent public graph/fresh-init verifiers.
 * A scope label or positive fixture result is insufficient for either gate.
 */
export function evaluateInstalledFreshStartReadiness(input = {}, adapters = {}) {
  return evaluateReadiness(input, adapters, "observed");
}

function evaluateReadiness({ plan, checkpoint, gateContext, evidence, facts = {} },
  { graphAdapter, freshInitializationAdapter }, scope) {
  const blockers = [], block = (code, message) => blockers.push({ code, message });
  let binding;
  try { binding = readinessBinding(plan, checkpoint, gateContext); }
  catch { block("invalid-fresh-start-plan", "Supply a pinned JSON plan, original lock identity, checkpoint and current invocation context."); }
  if (evidence?.scope !== scope) block("live-fresh-start-unavailable", scope === "synthetic-fixture"
    ? "Only synthetic source preparation is implemented; actual reset/cutover remains unavailable."
    : "Observed public evidence is required; synthetic preparation cannot authorize installed execution.");
  if (!text(evidence?.evidenceId)) block("readiness-evidence-missing", "An explicit checkpoint evidence reference is required.");
  if (!binding || !sameJson(binding, evidence?.binding ?? null)) block("readiness-binding-mismatch", "Evidence must bind this complete plan, checkpoint and current lock invocation.");
  const identity = plan?.receipt?.cutover;
  if (scope === "observed") {
    const confirmation = evidence?.cutoverConfirmation;
    if (!text(confirmation?.evidenceId) || confirmation.confirmed !== true
      || confirmation.freshStartChoice !== "graph-preserving-fresh-start"
      || confirmation.targetIdentity !== plan?.targetIdentity || confirmation.workspace !== identity?.workspace
      || confirmation.ingressOrigin !== identity?.ingress?.origin || confirmation.graphSourceReference !== identity?.graphSourceReference)
      block("exact-cutover-confirmation-missing", "Installed effects require the separately authorized exact graph-preserving target before any transition action.");
    for (const name of ["graphSource", "graphRecoveryCopy", "targetProvisioning", "graphImported", "freshInitialization", "quarantine", "authorityFence"])
      if (facts[name] !== undefined && (facts[name]?.scope !== "observed" || !text(facts[name]?.evidenceId)))
        block("installed-effect-unverified", `${name} must be independently observed; fixture facts cannot be promoted.`);
  }
  if (plan?.freshStartChoice !== "graph-preserving-fresh-start"
    || identity?.mode !== "graph-preserving-fresh-start" || !text(identity.graphSourceReference)
    || !text(identity.workspace) || !text(identity.ingress?.origin) || typeof identity.schema !== "string"
    || !/^sha256:[0-9a-f]{64}$/.test(identity.schema) || plan?.receipt?.schema !== identity.schema
    || !sameJson(identity, plan?.registration?.cutover))
    block("fresh-start-identity-missing", "Matching records must explicitly pin fresh-start mode, authored graph reference and new workspace/origin authority.");
  const expected = { transitionId: plan?.transitionId, revision: plan?.revision,
    sourceIdentity: plan?.sourceIdentity, targetIdentity: plan?.targetIdentity,
    graphSourceReference: identity?.graphSourceReference, workspace: identity?.workspace,
    ingressOrigin: identity?.ingress?.origin, schema: identity?.schema, checkpoint, gateContext };
  const adapterGate = (adapter, input, expectedBinding, pendingBlocker, failureCode) => {
    if (adapter?.scope !== scope || typeof adapter.validate !== "function") { blockers.push({ ...pendingBlocker }); return; }
    try {
      const result = adapter.validate(frozenCopy(input), frozenCopy(expectedBinding));
      if (!record(result) || result.status !== "verified" || !sameJson(result.binding ?? null, expectedBinding)
        || (scope === "observed" && (result.scope !== "observed" || !text(result.evidenceId))))
        block(failureCode, "The fixture provider must verify the exact public input and selected binding.");
    } catch { block(failureCode, "Public fixture validation failed; preserve supplied inputs and report the owner blocker."); }
  };
  const finalized = ["before-authority-switch", "before-admission", "before-forward-recovery"].includes(checkpoint);
  adapterGate(graphAdapter, { graphSource: plan?.graphSource, graphVerification: plan?.graphVerification,
    graphSourceFact: facts.graphSource, graphRecoveryCopy: facts.graphRecoveryCopy, graphImported: facts.graphImported },
  { ...expected, ...(finalized ? { graphReceipt: identity?.graphReceipt } : {}) }, pending[0], "graph-verification-rejected");

  if (["before-resume", "before-forward-recovery"].includes(checkpoint)) {
    const fence = facts.resumption;
    if (!text(fence?.evidenceId) || fence.verified !== true || fence.gateContext !== gateContext
      || fence.targetQuarantined !== true || fence.originalWriterStateConfirmed !== true)
      block("resumption-fence-unverified", "Reobserve this transition's original-writer fence and target quarantine under the current lock before resuming.");
  }
  if (finalized) {
    const graph = facts.graphSource, recovery = facts.graphRecoveryCopy, target = facts.targetProvisioning;
    const imported = facts.graphImported, fresh = facts.freshInitialization, quarantine = facts.quarantine;
    if (scope === "observed" && (imported?.graphReceipt !== identity?.graphReceipt || fresh?.freshInitReceipt !== identity?.freshInitReceipt))
      block("final-receipt-reference-mismatch", "Installed graph/fresh-init facts must identify exactly the finalized public receipt references before authority changes.");
    if (!text(identity?.graphReceipt) || !text(identity?.freshInitReceipt))
      block("final-graph-receipts-missing", "Final public graph-import and fresh-initialization receipt references are required.");
    if (!text(graph?.evidenceId) || graph.graphSourceReference !== identity?.graphSourceReference || graph.closureVerified !== true)
      block("graph-source-unverified", "Verify the pinned complete authored source/repertoire dependency closure.");
    if (!text(recovery?.evidenceId) || recovery.graphSourceReference !== identity?.graphSourceReference
      || recovery.restoreVerified !== true || recovery.immutable !== true || recovery.fullSourcePreserved !== true)
      block("graph-recovery-copy-unverified", "A separate immutable restore-verified full graph/source bundle copy is required; reduced Learning graph rows are insufficient.");
    if (!text(target?.evidenceId) || target.targetIdentity !== plan?.targetIdentity || target.workspace !== identity?.workspace
      || target.ingressOrigin !== identity?.ingress?.origin || target.schema !== identity?.schema
      || target.clean !== true || target.independent !== true)
      block("clean-target-unverified", "Provision and verify an independent clean Learning target; do not rebind the current live database.");
    if (!text(imported?.evidenceId) || imported.targetIdentity !== plan?.targetIdentity
      || imported.graphSourceReference !== identity?.graphSourceReference || imported.graphReceipt == null)
      block("graph-import-unverified", "Graph import must consume the exact pinned public source and return its opaque receipt.");
    if (!text(fresh?.evidenceId) || fresh.freshInitReceipt == null || fresh.targetIdentity !== plan?.targetIdentity
      || fresh.workspace !== identity?.workspace || fresh.ingressOrigin !== identity?.ingress?.origin)
      block("fresh-initialization-unverified", "Bind fresh-state initialization evidence to this clean target and workspace.");
    if (!text(quarantine?.evidenceId) || quarantine.targetIdentity !== plan?.targetIdentity || quarantine.workspace !== identity?.workspace
      || quarantine.ingressOrigin !== identity?.ingress?.origin || quarantine.graphSourceReference !== identity?.graphSourceReference
      || quarantine.schema !== identity?.schema
      || quarantine.readOnly !== true || quarantine.maintenanceDisabled !== true
      || quarantine.mutatingTrafficDisabled !== true || quarantine.graphClosureVerified !== true)
      block("target-quarantine-unverified", "Verify graph semantics and target authority while maintenance/write-capable traffic remain disabled.");
    const isolation = evidence?.authorityIsolation;
    if (!text(isolation?.evidenceId) || isolation.targetIdentity !== plan?.targetIdentity
      || isolation.workspace !== identity?.workspace || isolation.ingressOrigin !== identity?.ingress?.origin
      || isolation.oldAuthorityDisposition !== "isolated" || isolation.oldReplayDisabled !== true)
      block("new-authority-isolation-unverified", "Prove old cached authority cannot replay into the new workspace/origin/token authority; no old-intent drain is required.");
    const confirmation = evidence?.cutoverConfirmation;
    if (!text(confirmation?.evidenceId) || confirmation.confirmed !== true
      || confirmation.freshStartChoice !== "graph-preserving-fresh-start"
      || confirmation.targetIdentity !== plan?.targetIdentity || confirmation.workspace !== identity?.workspace
      || confirmation.ingressOrigin !== identity?.ingress?.origin || confirmation.graphSourceReference !== identity?.graphSourceReference)
      block("exact-cutover-confirmation-missing", "Separate exact user confirmation must bind the graph-preserving fresh-start choice and this pinned new authority.");
  }
  adapterGate(freshInitializationAdapter, { freshInitialization: plan?.freshInitialization,
    verification: facts.freshInitialization, isolation: evidence?.authorityIsolation },
  { ...expected, ...(finalized ? { freshInitReceipt: identity?.freshInitReceipt } : {}) },
  pending[1], "fresh-initialization-rejected");
  if (["before-admission", "before-forward-recovery"].includes(checkpoint)) {
    const publication = facts.publication;
    if (!text(publication?.evidenceId) || publication.registrationMatches !== true || publication.receiptMatches !== true || publication.durable !== true)
      block("publication-unverified", "Both durable v2 records must match the finalized target/graph/fresh-init identities.");
  }
  if (checkpoint === "before-admission" && (evidence?.targetAdmission?.confirmed !== true
    || evidence.targetAdmission.targetIdentity !== plan?.targetIdentity || evidence.targetAdmission.workspace !== identity?.workspace
    || evidence.targetAdmission.ingressOrigin !== identity?.ingress?.origin
    || !text(evidence.targetAdmission.evidenceId))) block("target-admission-unconfirmed", "Confirm exact target admission after durable publication.");
  if (checkpoint === "before-forward-recovery" && (evidence?.forwardRecovery?.preservesCurrentTarget !== true
    || evidence.forwardRecovery.restoresOlderSnapshot !== false || !text(evidence.forwardRecovery.evidenceId)
    || facts.resumption?.originalFenced !== true))
    block("forward-recovery-unverified", "Preserve effects already accepted by this fresh target; no automatic older-snapshot fallback.");
  return Object.freeze({ status: blockers.length ? "blocked" : "ready", evidenceId: text(evidence?.evidenceId) ? evidence.evidenceId : null,
    binding: binding ?? null, scope, liveExecutionAllowed: false,
    blockers: Object.freeze(blockers.map(Object.freeze)), production: scope === "synthetic-fixture"
      ? Object.freeze({ status: "blocked", blockers: pending })
      : Object.freeze({ status: blockers.length ? "blocked" : "ready", blockers: Object.freeze(blockers.map(Object.freeze)) }) });
}
