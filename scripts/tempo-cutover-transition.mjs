// Preparatory launcher lifecycle only. There is deliberately no CLI or live
// service/database adapter: the existing launcher must supply authorized ports.
import { createHash, randomUUID } from "node:crypto";
import { closeSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync,
  renameSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";
import { acquireTargetLock, atomicJson } from "./tempo-deployment.mjs";

export class CutoverBlocked extends Error {
  constructor(code, message) { super(message); this.name = "CutoverBlocked"; this.code = code; }
}

const blocked = (code, message) => { throw new CutoverBlocked(code, message); };
const fingerprint = value => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const byteFingerprint = value => value === null ? null : createHash("sha256").update(value).digest("hex");
const copy = value => structuredClone(value);
const encoded = bytes => bytes === null ? null : Buffer.from(bytes).toString("base64");
const decoded = bytes => bytes === null ? null : Buffer.from(bytes, "base64");
const candidate = plan => ({ registration: Buffer.from(`${JSON.stringify(plan.registration, null, 2)}\n`),
  receipt: Buffer.from(`${JSON.stringify(plan.receipt, null, 2)}\n`) });
const sameBytes = (left, right) => left === null || right === null ? left === right : Buffer.from(left).equals(Buffer.from(right));
const matchingPair = (left, right) => sameBytes(left.registration, right.registration) && sameBytes(left.receipt, right.receipt);

// Public readiness protocol: object keys sorted recursively, arrays ordered,
// JSON scalars only. Each lock acquisition supplies a fresh gateContext nonce.
function canonicalJson(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value)) return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && Object.getPrototypeOf(value) === Object.prototype)
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  blocked("cutover_plan_not_json", "Cutover evidence and plans must contain JSON values only.");
}

export function cutoverReleaseBinding(plan, checkpoint, gateContext) {
  return { version: 1, checkpoint, gateContext,
    planSha256: createHash("sha256").update(canonicalJson(plan)).digest("hex") };
}

function syncDirectory(path) {
  const descriptor = openSync(dirname(path), "r");
  try { fsyncSync(descriptor); } finally { closeSync(descriptor); }
}

// Exact bytes (including absence) matter for restoration. atomicJson remains the
// existing durable JSON writer; this bounded counterpart preserves raw records.
function atomicBytes(path, bytes) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  if (bytes === null) {
    if (existsSync(path)) { unlinkSync(path); syncDirectory(path); }
    return;
  }
  const temporary = `${path}.${randomUUID()}.partial`;
  let created = false;
  try {
    const descriptor = openSync(temporary, "wx", 0o600); created = true;
    try { writeFileSync(descriptor, bytes); fsyncSync(descriptor); } finally { closeSync(descriptor); }
    renameSync(temporary, path); created = false;
    syncDirectory(path);
  } finally { if (created) unlinkSync(temporary); }
}

/** Filesystem state only; paths must be supplied by the existing composition root.
 * withLock uses its existing stateDirectory/maintenance.lock, never a second lock.
 * The journal is private recovery evidence and must not be published to GitHub.
 */
export function createCutoverState({ stateDirectory, registrationPath, receiptPath, lockIdentity }) {
  const journalPath = join(stateDirectory, "cutover-transition.json");
  if (new Set([registrationPath, receiptPath, journalPath, join(stateDirectory, "maintenance.lock")].map(path => resolve(path))).size !== 4)
    blocked("cutover_state_paths_overlap", "Original lock, transition journal, registration and receipt need distinct explicit paths.");
  return {
    lockIdentity,
    async withLock(callback) {
      const release = acquireTargetLock(stateDirectory);
      try { return await callback(); } finally { release(); }
    },
    readJournal: () => existsSync(journalPath) ? JSON.parse(readFileSync(journalPath, "utf8")) : null,
    saveJournal: journal => atomicJson(journalPath, journal),
    readPublication: () => ({ registration: existsSync(registrationPath) ? readFileSync(registrationPath) : null,
      receipt: existsSync(receiptPath) ? readFileSync(receiptPath) : null }),
    writeRegistration: bytes => atomicBytes(registrationPath, bytes),
    writeReceipt: bytes => atomicBytes(receiptPath, bytes),
    confirmPublicationDurability: () => {
      for (const path of [registrationPath, receiptPath]) {
        const descriptor = openSync(path, "r");
        try { fsyncSync(descriptor); } finally { closeSync(descriptor); }
        syncDirectory(path);
      }
      return { durable: true, evidenceId: fingerprint([encoded(readFileSync(registrationPath)), encoded(readFileSync(receiptPath))]) };
    },
  };
}

const stages = [
  { started: "fencing", done: "fenced", action: "fenceLegacy", evidence: "fence" },
  { started: "snapshot-started", done: "source-verified", action: "snapshotSource", evidence: "sourceSnapshot" },
  { started: "import-started", done: "import-verified", action: "importHistory", evidence: "imported" },
  { started: "quarantine-started", done: "quarantined", action: "quarantineTarget", evidence: "quarantine" },
  { started: "target-backup-started", done: "target-verified", action: "backupTarget", evidence: "targetBackup" },
];
const phases = ["intent-recorded", ...stages.flatMap(stage => [stage.started, stage.done]),
  "registration-publication-started", "registration-published", "receipt-publication-started", "published",
  "activation-started", "complete", "restoration-started", "original-restored"];

function assertPlan(plan) {
  if (plan?.version !== 1 || !plan.transitionId || !plan.revision || !plan.sourceIdentity || !plan.targetIdentity
    || plan.lockIdentity !== plan.sourceIdentity || !plan.registration || !plan.receipt || !plan.original)
    blocked("cutover_plan_missing", "A complete pinned planner result and accepted transfer contract are required.");
}

function assertJournal(journal, plan) {
  if (journal?.version !== 1 || journal.planFingerprint !== fingerprint(plan)
    || journal.transitionId !== plan.transitionId || !phases.includes(journal.phase)
    || !journal.original || !journal.evidence || !Object.hasOwn(journal.original, "registration") || !Object.hasOwn(journal.original, "receipt"))
    blocked("cutover_journal_mismatch", "Preserve the journal: its transition, pinned plan or original publication is inconsistent.");
  try {
    for (const name of ["registration", "receipt"]) {
      const raw = decoded(journal.original[name]);
      if (encoded(raw) !== journal.original[name] || byteFingerprint(raw) !== journal.original.digests?.[name]
        || !isDeepStrictEqual(raw === null ? null : JSON.parse(raw.toString("utf8")), plan.original[name]))
        blocked("cutover_original_corrupt", "Original publication bytes or their identity do not match the durable intent.");
    }
  } catch (error) {
    if (error instanceof CutoverBlocked) throw error;
    blocked("cutover_original_corrupt", "Original publication recovery bytes are invalid; preserve the journal.");
  }
  if (journal.finalized) {
    assertFinalized(plan, journal.finalized);
    if (!matchingPair(publicationBytes(journal), candidate(journal.finalized)))
      blocked("cutover_final_publication_corrupt", "Finalized publication bytes differ from its pinned records.");
  }
  if (!["restoration-started", "original-restored"].includes(journal.phase)) {
    for (const stage of stages)
      if (phases.indexOf(journal.phase) >= phases.indexOf(stage.done))
        assertEvidence(stage.evidence, journal.evidence[stage.evidence], journal);
    if (phases.indexOf(journal.phase) >= phases.indexOf("registration-publication-started")) publicationBytes(journal);
    if (journal.phase === "complete") assertEvidence("activation", journal.evidence.activation, journal);
  }
}

async function releaseGate(effects, plan, checkpoint, journal, gateContext) {
  const selected = journal?.finalized ? { ...plan, ...journal.finalized } : plan;
  const result = await effects.assertReleaseGates(copy(selected), checkpoint, copy(journal?.evidence ?? {}), gateContext);
  if (result?.status !== "ready" || typeof result.evidenceId !== "string" || !result.evidenceId
    || !isDeepStrictEqual(result.binding, cutoverReleaseBinding(selected, checkpoint, gateContext)))
    blocked("cutover_release_blocked", `Required evidence is missing at ${checkpoint}.`);
}

function assertEvidence(name, evidence, journal) {
  if (evidence?.verified !== true || typeof evidence.evidenceId !== "string" || !evidence.evidenceId)
    blocked("cutover_effect_unverified", `${name} did not return verified evidence.`);
  if (name === "sourceSnapshot" && (!evidence.sourceStateId || evidence.restoreVerified !== true))
    blocked("cutover_source_unverified", "The final fenced source snapshot must be identified and restore-verified.");
  if (name === "imported" && (!evidence.transferReceipt
    || evidence.sourceStateId !== journal.evidence.sourceSnapshot?.sourceStateId))
    blocked("cutover_import_source_mismatch", "Import evidence must consume the exact final fenced source state.");
  if (name === "quarantine" && (evidence.readOnly !== true || !evidence.targetIdentity || !evidence.workspace
    || !Number.isInteger(evidence.schema) || evidence.maintenanceDisabled !== true || evidence.mutatingTrafficDisabled !== true))
    blocked("cutover_quarantine_unverified", "Target maintenance and mutating traffic must remain disabled.");
  if (name === "targetBackup" && evidence.restoreVerified !== true)
    blocked("cutover_target_backup_unverified", "The post-import target backup must be restore-verified.");
}

function originalBytes(journal) {
  return { registration: decoded(journal.original.registration), receipt: decoded(journal.original.receipt) };
}

function publicationBytes(journal) {
  if (!journal.publication || !journal.finalized)
    blocked("cutover_final_publication_missing", "The finalized accepted-transfer publication is missing. Keep both writers fenced.");
  return { registration: decoded(journal.publication.registration), receipt: decoded(journal.publication.receipt) };
}

function assertFinalized(plan, finalized) {
  if (!finalized?.registration || !finalized.receipt || !finalized.transferAcceptance)
    blocked("accepted-transfer-contract-pending", "An accepted final transfer contract and publication adapter are required.");
  for (const name of ["registration", "receipt"]) {
    const { cutover: initialIdentity, ...initialRecord } = plan[name];
    const { cutover: finalIdentity, ...finalRecord } = finalized[name];
    const { transferReceipt: initialTransfer, ...initialPinned } = initialIdentity ?? {};
    const { transferReceipt: finalTransfer, ...finalPinned } = finalIdentity ?? {};
    if (!initialIdentity || !finalIdentity || !isDeepStrictEqual(initialRecord, finalRecord)
      || !isDeepStrictEqual(initialPinned, finalPinned) || typeof finalTransfer !== "string" || !finalTransfer)
      blocked("cutover_final_identity_changed", "Final transfer publication changed pinned target/artifact identities.");
  }
  if (!isDeepStrictEqual(finalized.registration.cutover, finalized.receipt.cutover))
    blocked("cutover_final_identity_mismatch", "Final registration and receipt must carry exactly the same cutover identity.");
  for (const name of ["transitionId", "revision", "sourceIdentity", "targetIdentity", "lockIdentity", "original"])
    if (Object.hasOwn(finalized, name) && !isDeepStrictEqual(finalized[name], plan[name]))
      blocked("cutover_final_identity_changed", "The final publication adapter changed the pinned transition or original authority.");
}

/**
 * Semantic ports: assertReleaseGates, fenceLegacy, snapshotSource, importHistory,
 * quarantineTarget, backupTarget, finalizePublication, activateTarget, verifyActivation.
 * On interrupted effects, recoverInterrupted must positively return either
 * {status:'completed', evidence} or {status:'not-started'}; unknown blocks.
 * Each port receives {plan, facts, operationKey}; no converter internals, queue
 * endpoint or mutating smoke test is called by this module.
 */
export async function executeCutoverTransition(plan, { state, effects, recovery } = {}) {
  assertPlan(plan);
  plan = copy(plan);
  if (effects.executionScope !== "synthetic-fixture")
    blocked("accepted-transfer-contract-pending", "Production execution is unavailable: this preparatory lifecycle accepts synthetic fixture effects only.");
  if (state.lockIdentity !== plan.lockIdentity)
    blocked("cutover_lock_identity_mismatch", "The original target lock/state authority must remain fixed throughout the transition.");
  return state.withLock(async () => {
    const gateContext = randomUUID();
    let journal = await state.readJournal();
    const resumed = Boolean(journal);
    if (!journal) {
      await releaseGate(effects, plan, "before-fence", null, gateContext);
      const original = await state.readPublication();
      try {
        for (const name of ["registration", "receipt"]) {
          const actual = original[name] === null ? null : JSON.parse(Buffer.from(original[name]).toString("utf8"));
          if (!isDeepStrictEqual(actual, plan.original[name])) blocked("cutover_original_changed", "Original publication differs from the approved planner input.");
        }
      } catch (error) {
        if (error instanceof CutoverBlocked) throw error;
        blocked("cutover_original_invalid", "Original publication cannot be interpreted; preserve it before planning.");
      }
      journal = { version: 1, transitionId: plan.transitionId, planFingerprint: fingerprint(plan),
        phase: "intent-recorded", original: { registration: encoded(original.registration), receipt: encoded(original.receipt),
          digests: { registration: byteFingerprint(original.registration), receipt: byteFingerprint(original.receipt) } }, evidence: {} };
      await state.saveJournal(copy(journal));
    }
    assertJournal(journal, plan);
    const persist = async phase => { journal.phase = phase; await state.saveJournal(copy(journal)); };
    const currentPair = () => state.readPublication();
    const confirmPublication = async () => {
      const expected = publicationBytes(journal);
      if (!matchingPair(await currentPair(), expected))
        blocked("cutover_publication_mismatch", "Registration and receipt do not match the finalized pair.");
      const durable = await state.confirmPublicationDurability();
      if (durable?.durable !== true || !durable.evidenceId || !matchingPair(await currentPair(), expected))
        blocked("cutover_publication_not_durable", "Matching registration and receipt durability has not been established.");
      journal.evidence.publication = { evidenceId: durable.evidenceId, registrationMatches: true, receiptMatches: true, durable: true };
    };
    if (["restoration-started", "original-restored"].includes(journal.phase))
      blocked("cutover_restoration_pending", "This transition is restoring or has restored original authority; do not activate it.");
    const selectedPlan = () => journal.finalized ? { ...plan, ...journal.finalized } : plan;
    if (journal.phase === "activation-started" && recovery !== "forward")
      blocked("cutover_forward_recovery_required", "Activation may have accepted effects. Keep legacy fenced and explicitly repair the selected target.");
    if (resumed && journal.phase !== "complete") {
      if (journal.phase !== "intent-recorded") {
        const fence = await effects.verifyResumptionFence?.({ plan: copy(selectedPlan()), facts: copy(journal.evidence), phase: journal.phase, gateContext });
        if (fence?.verified !== true || !fence.evidenceId || fence.gateContext !== gateContext
          || fence.legacyFenced !== true || fence.targetQuarantined !== true || fence.originalBrowserWorkRecoverable !== true)
          blocked("cutover_resume_fence_unverified", "Fresh fence and original-authority pending-work evidence is required before resuming.");
        journal.evidence.resumptionFence = copy(fence);
      }
      await releaseGate(effects, plan, journal.phase === "intent-recorded" ? "before-fence" : "before-resume", journal, gateContext);
    }
    if (phases.indexOf(journal.phase) >= phases.indexOf("registration-publication-started")) {
      if (!matchingPair(await currentPair(), publicationBytes(journal)))
        blocked("cutover_publication_mismatch", "Registration and receipt do not match the pinned target pair. Keep both writers fenced until explicit repair.");
      if (journal.phase === "complete") return copy(journal);
      if (journal.phase === "activation-started") {
        if (recovery !== "forward") blocked("cutover_forward_recovery_required", "Activation may have accepted effects. Keep legacy fenced and explicitly repair the selected target.");
        await releaseGate(effects, plan, "before-forward-recovery", journal, gateContext);
        const evidence = await effects.recoverTarget({ plan: copy(selectedPlan()), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:forward-recovery` });
        assertEvidence("activation", evidence, journal); journal.evidence.activation = copy(evidence);
        await persist("complete"); return copy(journal);
      }
      // A pair fully persisted before its completion journal write is safely
      // reconcilable. A half pair was rejected above; never guess its authority.
      await confirmPublication(); await persist("published");
    } else {
      if (!matchingPair(await currentPair(), originalBytes(journal)))
        blocked("cutover_original_changed", "Original publication changed during the fenced transition.");
      for (const stage of stages) {
        if (phases.indexOf(journal.phase) >= phases.indexOf(stage.done)) continue;
        let evidence;
        const context = { plan: copy(plan), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:${stage.started}` };
        if (journal.phase === stage.started) {
          const result = await effects.recoverInterrupted?.({ ...context, phase: stage.started });
          if (result?.status === "completed") evidence = result.evidence;
          else if (result?.status !== "not-started")
            blocked("cutover_effect_uncertain", `The interrupted ${stage.started} effect requires positive reconciliation.`);
        } else await persist(stage.started);
        if (!evidence) evidence = await effects[stage.action](context);
        assertEvidence(stage.evidence, evidence, journal);
        journal.evidence[stage.evidence] = copy(evidence);
        await persist(stage.done);
      }
      if (!journal.finalized) {
        if (typeof effects.finalizePublication !== "function")
          blocked("accepted-transfer-contract-pending", "No accepted final transfer/publication adapter exists; target publication stays disabled.");
        const finalized = await effects.finalizePublication({ plan: copy(plan),
          transferReceipt: copy(journal.evidence.imported.transferReceipt), facts: copy(journal.evidence) });
        assertFinalized(plan, finalized);
        journal.finalized = copy({ registration: finalized.registration, receipt: finalized.receipt, transferAcceptance: finalized.transferAcceptance });
        const publication = candidate(finalized);
        journal.publication = { registration: encoded(publication.registration), receipt: encoded(publication.receipt) };
        await state.saveJournal(copy(journal));
      }
      await releaseGate(effects, plan, "before-publication", journal, gateContext);
      await persist("registration-publication-started");
      await state.writeRegistration(publicationBytes(journal).registration);
      await persist("registration-published");
      await persist("receipt-publication-started");
      await state.writeReceipt(publicationBytes(journal).receipt);
      await confirmPublication();
      await persist("published");
    }
    await releaseGate(effects, plan, "before-activation", journal, gateContext);
    await persist("activation-started");
    await effects.activateTarget({ plan: copy(selectedPlan()), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:activation` });
    const evidence = await effects.verifyActivation({ plan: copy(selectedPlan()), facts: copy(journal.evidence) });
    assertEvidence("activation", evidence, journal); journal.evidence.activation = copy(evidence);
    await persist("complete");
    return copy(journal);
  });
}

/** Explicit recovery only. A crash/timeout never constitutes zero-write proof.
 * proveOriginalRestoration must freshly fence both sides, retain original-authority
 * browser work, verify the original source untouched, and prove no target-only
 * effects (including issuance, intents, receipts and maintenance) were accepted.
 * No database restore or legacy service restart is performed here.
 */
export async function restoreOriginalPublication(plan, { state, effects, request } = {}) {
  assertPlan(plan);
  if (effects.executionScope !== "synthetic-fixture")
    blocked("accepted-transfer-contract-pending", "Production restoration is unavailable while accepted transfer/authority contracts are pending.");
  if (state.lockIdentity !== plan.lockIdentity)
    blocked("cutover_lock_identity_mismatch", "Original restoration must retain the original target lock/state authority.");
  if (request?.transitionId !== plan.transitionId || request?.restoreOriginal !== true)
    blocked("cutover_restoration_not_requested", "Original authority restoration requires an explicit request for this transition.");
  return state.withLock(async () => {
    const gateContext = randomUUID();
    const journal = await state.readJournal(); assertJournal(journal, plan);
    const selected = journal.finalized ? { ...plan, ...journal.finalized } : plan;
    const proof = await effects.proveOriginalRestoration({ plan: copy(selected), facts: copy(journal.evidence), phase: journal.phase, gateContext });
    if (proof?.transitionId !== plan.transitionId || proof.targetIdentity !== plan.targetIdentity
      || proof.targetOnlyEffects !== "none" || proof.targetFenced !== true || proof.legacyFenced !== true
      || proof.originalSourceUnchanged !== true || proof.originalBrowserWorkRecoverable !== true || !proof.evidenceId
      || !isDeepStrictEqual(proof.binding, cutoverReleaseBinding(selected, "before-original-restoration", gateContext)))
      blocked("cutover_restoration_unproven", "Original restoration is blocked until fresh explicit zero-effect and preservation evidence is supplied.");
    journal.phase = "restoration-started"; journal.evidence.restoration = copy(proof);
    await state.saveJournal(copy(journal));
    const original = originalBytes(journal);
    await state.writeRegistration(original.registration);
    await state.writeReceipt(original.receipt);
    if (!matchingPair(await state.readPublication(), original)) blocked("cutover_restoration_mismatch", "Original publication restoration is incomplete. Keep both writers fenced.");
    journal.phase = "original-restored"; await state.saveJournal(copy(journal));
    return copy(journal);
  });
}
