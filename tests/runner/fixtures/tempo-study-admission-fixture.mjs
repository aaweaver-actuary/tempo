// Canonical observed-PROTOCOL fixture: injected fakes and disposable files only.
// No installed system, database, graph acceptance or actual user approval is observed.
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { planLauncherFreshStart } from "../../../scripts/tempo-cutover-plan.mjs";
import { evaluateInstalledFreshStartReadiness, readinessBinding } from "../../../scripts/tempo-cutover-readiness.mjs";
import { createCutoverState, createInstalledFreshStartEffects } from "../../../scripts/tempo-cutover-transition.mjs";
const cloned = value => structuredClone(value);
export function createAdmissionProtocolFixture(t, { configureInput } = {}) {
  const directory = mkdtempSync(join(tmpdir(), "tempo-installed-admission-protocol-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const registrationPath = join(directory, "registration.json"), receiptPath = join(directory, "receipt.json");
  const admissionPath = join(directory, "installed-admission.json");
  const input = JSON.parse(readFileSync(new URL("../../../scripts/tests/fixtures/tempo-cutover-plan/publication-input.json", import.meta.url)));
  input.current.registration.disposable = false;
  input.proposed.registration.disposable = false;
  if (configureInput) configureInput(input, { directory, registrationPath, receiptPath, admissionPath });
  const result = planLauncherFreshStart(input); assert.equal(result.status, "planned");
  const plan = cloned(result.plan);
  writeFileSync(registrationPath, JSON.stringify(plan.original.registration), { mode: 0o600 });
  writeFileSync(receiptPath, JSON.stringify(plan.original.receipt), { mode: 0o600 });
  const state = createCutoverState({ stateDirectory: directory, registrationPath, receiptPath, lockIdentity: plan.lockIdentity, admissionPath });
  const events = [], infra = { originalWriter: true, targetWriter: false };
  const observed = (args, extra = {}) => ({ scope: "observed", verified: true, evidenceId: "protocol-fixture-only",
    binding: cloned(args.binding), ...extra });
  const target = { targetIdentity: plan.targetIdentity, workspace: plan.registration.cutover.workspace,
    ingressOrigin: plan.registration.cutover.ingress.origin, schema: plan.registration.cutover.schema };
  const source = { graphSourceReference: plan.registration.cutover.graphSourceReference };
  const effect = (name, extra) => async args => { events.push(name); return observed(args, typeof extra === "function" ? extra(args) : extra); };
  const operations = {
    verifyGraphSource: effect("source", { ...source, closureVerified: true }),
    createVerifiedGraphCopy: effect("copy", { ...source, restoreVerified: true, immutable: true, fullSourcePreserved: true }),
    provisionCleanTarget: effect("provision", { ...target, clean: true, independent: true }),
    importGraph: effect("import", { ...target, ...source, graphReceipt: plan.registration.cutover.graphReceipt }),
    initializeFreshLearning: effect("initialize", { ...target, freshInitReceipt: plan.registration.cutover.freshInitReceipt }),
    verifyGraphTarget: effect("quarantine", { ...target, ...source, graphClosureVerified: true, readOnly: true,
      maintenanceDisabled: true, mutatingTrafficDisabled: true }),
    finalizeFreshStartPublication: async ({ plan: selected }) => ({ registration: cloned(selected.registration), receipt: cloned(selected.receipt),
      graphVerification: cloned(selected.graphVerification), freshInitialization: cloned(selected.freshInitialization) }),
    fenceOriginalAuthority: effect("fence", () => { infra.originalWriter = false; return { originalFenced: true, targetQuarantined: true }; }),
    admitTarget: async args => {
      events.push("admit"); assert.equal(state.readJournal().phase, "activation-started"); assert.equal(infra.originalWriter, false);
      infra.targetWriter = true; return observed(args);
    },
    verifyAdmission: async args => { events.push("verify-admission"); return observed(args, { phase: "complete", operationBinding: cloned(args.operationBinding) }); },
    observeResumption: async args => observed(args, { gateContext: args.gateContext, targetQuarantined: !infra.targetWriter,
      originalWriterStateConfirmed: true, originalFenced: !infra.originalWriter }),
    recoverInterrupted: async args => observed(args, { status: "not-started" }),
    recoverTarget: async args => { events.push("forward-repair"); infra.targetWriter = true;
      return observed(args, { phase: "complete", operationBinding: cloned(args.operationBinding) }); },
  };
  const evidence = ({ plan: selected, checkpoint, gateContext }) => {
    events.push(checkpoint);
    const identity = selected.registration.cutover;
    return { scope: "observed", evidenceId: "protocol-fixture-evidence", binding: readinessBinding(selected, checkpoint, gateContext),
      cutoverConfirmation: { evidenceId: "fixture-confirmation", confirmed: true, freshStartChoice: "graph-preserving-fresh-start",
        targetIdentity: selected.targetIdentity, workspace: identity.workspace, ingressOrigin: identity.ingress.origin,
        graphSourceReference: identity.graphSourceReference },
      targetAdmission: { evidenceId: "fixture-admission-choice", confirmed: true, targetIdentity: selected.targetIdentity,
        workspace: identity.workspace, ingressOrigin: identity.ingress.origin },
      authorityIsolation: { evidenceId: "fixture-isolation", targetIdentity: selected.targetIdentity, workspace: identity.workspace,
        ingressOrigin: identity.ingress.origin, oldAuthorityDisposition: "isolated", oldReplayDisabled: true },
      forwardRecovery: { evidenceId: "fixture-forward-choice", preservesCurrentTarget: true, restoresOlderSnapshot: false } };
  };
  const verifier = { scope: "observed", validate: (input, binding) => ({ status: "verified", scope: "observed",
    evidenceId: "protocol-fixture-verifier", binding }) };
  const adapters = { graphAdapter: verifier, freshInitializationAdapter: verifier };
  const config = { operations, readReleaseEvidence: evidence,
    assessReadiness: args => evaluateInstalledFreshStartReadiness(args, adapters) };
  const effects = () => createInstalledFreshStartEffects(config);
  return { plan, state, events, infra, operations, config, effects, adapters, directory, registrationPath, receiptPath, admissionPath };
}
