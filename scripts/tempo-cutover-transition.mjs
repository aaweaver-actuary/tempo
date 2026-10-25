// Graph-preserving fresh-start preparation. There is deliberately no CLI or live
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
  ["graph-source-started", "graph-source-verified", "verifyGraphSource", "graphSource"],
  ["graph-copy-started", "graph-copy-verified", "createVerifiedGraphCopy", "graphRecoveryCopy"],
  ["target-provisioning-started", "target-provisioned", "provisionCleanTarget", "targetProvisioning"],
  ["graph-import-started", "graph-imported", "importGraph", "graphImported"],
  ["fresh-initialization-started", "fresh-initialized", "initializeFreshLearning", "freshInitialization"],
  ["graph-verification-started", "target-verified", "verifyGraphTarget", "quarantine"],
  ["admission-started", "authority-fenced", "fenceOriginalAuthority", "authorityFence"],
];
const phases = ["intent-recorded", ...stages.flatMap(([started, done]) => [started, done]),
  "registration-publication-started", "registration-published", "receipt-publication-started", "published", "activation-started", "complete"];
const order = phase => phases.indexOf(phase);
const selected = (plan, journal) => journal.finalized ? { ...plan, ...journal.finalized } : plan;
const identity = plan => plan.registration.cutover;
const isSchemaIdentity = value => typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);

function assertPlan(plan) {
  if (plan?.version !== 1 || !plan.transitionId || !plan.revision || !plan.sourceIdentity || !plan.targetIdentity
    || plan.lockIdentity !== plan.sourceIdentity || !plan.original || !plan.registration || !plan.receipt
    || plan.freshStartChoice !== "graph-preserving-fresh-start" || identity(plan)?.mode !== "graph-preserving-fresh-start" || !identity(plan).graphSourceReference
    || !identity(plan).workspace || !plan.graphSource)
    blocked("graph-contract-pending", "Supply an explicit graph-preserving fresh-start intent, graph source, and new target authority.");
  const schema = identity(plan).schema;
  if (!isSchemaIdentity(schema) || plan.receipt.schema !== schema || plan.receipt.cutover?.schema !== schema)
    blocked("fresh_start_schema_mismatch", "Pin one public Learning migration-map SHA-256 identity across the v2 registration and receipt.");
  if (!["registration", "receipt"].every(name => Object.hasOwn(plan.original, name)))
    blocked("fresh_start_prior_publication_unknown", "Record prior registration/receipt presence explicitly; a known absent receipt is allowed.");
}

function checkFact(name, fact, plan) {
  if (fact?.verified !== true || typeof fact.evidenceId !== "string" || !fact.evidenceId)
    blocked("fresh_start_evidence_missing", `${name} requires positive public-contract evidence.`);
  const target = identity(plan);
  if (["graphSource", "graphRecoveryCopy", "graphImported", "quarantine"].includes(name)
    && fact.graphSourceReference !== target.graphSourceReference)
    blocked("fresh_start_graph_mismatch", "The graph effect does not match the explicitly selected graph source.");
  if (["targetProvisioning", "graphImported", "freshInitialization", "quarantine"].includes(name)
    && fact.targetIdentity !== plan.targetIdentity)
    blocked("fresh_start_target_mismatch", "The graph effect belongs to another target.");
  if (["targetProvisioning", "freshInitialization", "quarantine"].includes(name)
    && (fact.workspace !== target.workspace || fact.ingressOrigin !== target.ingress.origin))
    blocked("fresh_start_authority_mismatch", "Fresh target workspace/origin does not match the pinned new authority.");
  if (["targetProvisioning", "quarantine"].includes(name)
    && (!isSchemaIdentity(fact.schema) || fact.schema !== target.schema))
    blocked("fresh_start_schema_mismatch", "Provisioning and quarantine must verify the exact pinned Learning migration-map SHA-256 identity.");
  if ((name === "graphSource" && fact.closureVerified !== true)
    || (name === "graphRecoveryCopy" && (fact.restoreVerified !== true || fact.immutable !== true || fact.fullSourcePreserved !== true))
    || (name === "targetProvisioning" && (fact.clean !== true || fact.independent !== true))
    || (name === "graphImported" && !fact.graphReceipt)
    || (name === "freshInitialization" && !fact.freshInitReceipt)
    || (name === "quarantine" && (fact.graphClosureVerified !== true || fact.readOnly !== true
      || fact.maintenanceDisabled !== true || fact.mutatingTrafficDisabled !== true))
    || (name === "authorityFence" && (fact.originalFenced !== true || fact.targetQuarantined !== true)))
    blocked("fresh_start_evidence_missing", `${name} did not establish its fresh-start boundary.`);
}

function assertFinalized(plan, finalized) {
  if (!finalized?.registration || !finalized.receipt || !finalized.graphVerification)
    blocked("graph-contract-pending", "Verified graph publication through its public owner is pending.");
  if (!finalized.freshInitialization)
    blocked("fresh-initialization-contract-pending", "Verified clean Learning initialization through its public owner is pending.");
  for (const name of ["registration", "receipt"]) {
    const { cutover: initial, ...priorRecord } = plan[name];
    const { cutover: final, ...finalRecord } = finalized[name];
    const { graphReceipt: priorGraph, freshInitReceipt: priorInit, ...pinned } = initial ?? {};
    const { graphReceipt, freshInitReceipt, ...finalPinned } = final ?? {};
    if (!isDeepStrictEqual(priorRecord, finalRecord) || !isDeepStrictEqual(pinned, finalPinned)
      || typeof graphReceipt !== "string" || !graphReceipt || typeof freshInitReceipt !== "string" || !freshInitReceipt
      || (priorGraph != null && priorGraph !== graphReceipt) || (priorInit != null && priorInit !== freshInitReceipt))
      blocked("fresh_start_identity_changed", "Final graph publication may fill only the two public receipt references.");
  }
  if (!isDeepStrictEqual(finalized.registration.cutover, finalized.receipt.cutover))
    blocked("fresh_start_publication_mismatch", "Registration and receipt must identify the same fresh-start target.");
}

function originalPair(journal) {
  return { registration: decoded(journal.original.registration), receipt: decoded(journal.original.receipt) };
}
function finalPair(journal) {
  if (!journal.publication || !journal.finalized) blocked("fresh_start_publication_missing", "Final fresh-start publication is missing.");
  return { registration: decoded(journal.publication.registration), receipt: decoded(journal.publication.receipt) };
}
function assertJournal(journal, plan) {
  if (journal?.version !== 1 || journal.mode !== "graph-preserving-fresh-start" || journal.planFingerprint !== fingerprint(plan)
    || journal.transitionId !== plan.transitionId || order(journal.phase) < 0 || !journal.original || !journal.evidence)
    blocked("fresh_start_journal_mismatch", "Preserve the journal: it does not match this pinned fresh-start intent.");
  try {
    for (const name of ["registration", "receipt"]) {
      const raw = decoded(journal.original[name]);
      if (encoded(raw) !== journal.original[name] || byteFingerprint(raw) !== journal.original.digests?.[name]
        || !isDeepStrictEqual(raw === null ? null : JSON.parse(raw.toString("utf8")), plan.original[name]))
        blocked("fresh_start_original_corrupt", "Original publication identity bytes do not match the durable intent.");
    }
  } catch (error) {
    if (error instanceof CutoverBlocked) throw error;
    blocked("fresh_start_original_corrupt", "Original publication identity evidence is corrupt.");
  }
  for (const [, done, , name] of stages) if (order(journal.phase) >= order(done)) checkFact(name, journal.evidence[name], plan);
  if (journal.finalized) {
    assertFinalized(plan, journal.finalized);
    if (!matchingPair(finalPair(journal), candidate(journal.finalized)))
      blocked("fresh_start_publication_corrupt", "Finalized publication bytes do not match the selected records.");
  }
  if (journal.phase === "complete") checkFact("admission", journal.evidence.admission, plan);
}

async function gate(effects, plan, journal, checkpoint, gateContext) {
  const current = selected(plan, journal);
  const result = await effects.assertReleaseGates(copy(current), checkpoint, copy(journal.evidence), gateContext);
  if (result?.status !== "ready" || !result.evidenceId
    || !isDeepStrictEqual(result.binding, cutoverReleaseBinding(current, checkpoint, gateContext)))
    blocked("fresh_start_gate_blocked", `Fresh-start approval/evidence is missing at ${checkpoint}.`);
}

/** Fresh graph only. No historical reviews/schedules, background work, browser
 * drain/replay or old-backup acceptance is required or exposed. Semantic graph
 * and fresh-Learning ports remain owner-supplied public contracts.
 * Original DB content is never changed by this module; its only authority
 * effect is explicit fencing AFTER graph verification and cutover confirmation.
 */
export async function executeFreshStartTransition(plan, { state, effects, recovery } = {}) {
  assertPlan(plan); plan = copy(plan);
  if (effects?.executionScope !== "synthetic-fixture")
    blocked("graph-contract-pending", "Only disposable synthetic effects are wired; actual graph/cutover execution is unavailable.");
  if (typeof effects.verifyGraphSource !== "function")
    blocked("graph-contract-pending", "The graph source/closure public contract is not connected.");
  if (typeof effects.initializeFreshLearning !== "function")
    blocked("fresh-initialization-contract-pending", "The clean Learning initialization public contract is not connected.");
  if (state.lockIdentity !== plan.lockIdentity)
    blocked("fresh_start_lock_mismatch", "Retain the original lock/state identity for the whole fresh-start transition.");
  return state.withLock(async () => {
    const gateContext = randomUUID(); let journal = await state.readJournal(); const resumed = Boolean(journal);
    if (!journal) {
      journal = { version: 1, mode: "graph-preserving-fresh-start", transitionId: plan.transitionId,
        planFingerprint: fingerprint(plan), phase: "intent-recorded", evidence: {} };
      await gate(effects, plan, journal, "before-preparation", gateContext);
      const original = await state.readPublication();
      for (const name of ["registration", "receipt"]) {
        const raw = original[name]; let parsed;
        try { parsed = raw === null ? null : JSON.parse(Buffer.from(raw).toString("utf8")); }
        catch { blocked("fresh_start_original_invalid", "Original publication identity cannot be interpreted."); }
        if (!isDeepStrictEqual(parsed, plan.original[name])) blocked("fresh_start_original_changed", "Original publication changed after planning.");
      }
      journal.original = { registration: encoded(original.registration), receipt: encoded(original.receipt),
        digests: { registration: byteFingerprint(original.registration), receipt: byteFingerprint(original.receipt) } };
      await state.saveJournal(copy(journal));
    }
    assertJournal(journal, plan);
    const persist = async phase => { journal.phase = phase; await state.saveJournal(copy(journal)); };
    const confirmPublication = async () => {
      if (!matchingPair(await state.readPublication(), finalPair(journal)))
        blocked("fresh_start_publication_mismatch", "Split or unexpected publication blocks admission; never guess target authority.");
      const durable = await state.confirmPublicationDurability();
      if (durable?.durable !== true || !durable.evidenceId || !matchingPair(await state.readPublication(), finalPair(journal)))
        blocked("fresh_start_publication_not_durable", "The matching fresh-start publication is not durably confirmed.");
      journal.evidence.publication = { evidenceId: durable.evidenceId, registrationMatches: true, receiptMatches: true, durable: true };
    };
    if (journal.phase === "complete") {
      if (!matchingPair(await state.readPublication(), finalPair(journal))) blocked("fresh_start_publication_mismatch", "Completed publication changed.");
      return copy(journal);
    }
    if (journal.phase === "activation-started" && recovery !== "forward")
      blocked("fresh_start_forward_recovery_required", "Fresh target acceptance is uncertain; explicitly repair this target without old-authority fallback.");
    if (resumed) {
      const observation = await effects.observeResumption?.({ plan: copy(selected(plan, journal)), facts: copy(journal.evidence), phase: journal.phase, gateContext });
      if (observation?.verified !== true || !observation.evidenceId || observation.gateContext !== gateContext
        || observation.targetQuarantined !== true || observation.originalWriterStateConfirmed !== true || (order(journal.phase) >= order("authority-fenced") && observation.originalFenced !== true))
        blocked("fresh_start_resume_unverified", "Fresh observation of this transition's target and authority fence is required.");
      journal.evidence.resumption = copy(observation);
      await gate(effects, plan, journal, "before-resume", gateContext);
    }
    if (order(journal.phase) >= order("registration-publication-started")) {
      await confirmPublication();
      if (journal.phase === "activation-started") {
        await gate(effects, plan, journal, "before-forward-recovery", gateContext);
        const fact = await effects.recoverTarget({ plan: copy(selected(plan, journal)), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:forward-recovery` });
        checkFact("admission", fact, plan); journal.evidence.admission = copy(fact); await persist("complete"); return copy(journal);
      }
      await persist("published");
    } else {
      if (!matchingPair(await state.readPublication(), originalPair(journal))) blocked("fresh_start_original_changed", "Original publication changed during preparation.");
      for (const [started, done, action, name] of stages) {
        if (order(journal.phase) >= order(done)) continue;
        if (name === "authorityFence") {
          if (!journal.finalized) {
            const final = await effects.finalizeFreshStartPublication?.({ plan: copy(plan), facts: copy(journal.evidence) });
            assertFinalized(plan, final);
            journal.finalized = copy({ registration: final.registration, receipt: final.receipt,
              graphVerification: final.graphVerification, freshInitialization: final.freshInitialization });
            const records = candidate(final); journal.publication = { registration: encoded(records.registration), receipt: encoded(records.receipt) };
            await state.saveJournal(copy(journal));
          }
          await gate(effects, plan, journal, "before-authority-switch", gateContext);
        }
        const args = { plan: copy(selected(plan, journal)), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:${started}` };
        let fact;
        if (journal.phase === started) {
          const result = await effects.recoverInterrupted?.({ ...args, phase: started });
          if (result?.status === "completed") fact = result.evidence;
          else if (result?.status !== "not-started") blocked("fresh_start_effect_uncertain", "Reconcile the interrupted fresh-start effect; do not reset or blindly repeat it.");
        } else await persist(started);
        if (!fact) fact = await effects[action](args);
        checkFact(name, fact, plan); journal.evidence[name] = copy(fact); await persist(done);
      }
      await persist("registration-publication-started"); await state.writeRegistration(finalPair(journal).registration);
      await persist("registration-published"); await persist("receipt-publication-started"); await state.writeReceipt(finalPair(journal).receipt);
      await confirmPublication(); await persist("published");
    }
    await gate(effects, plan, journal, "before-admission", gateContext);
    await persist("activation-started");
    await effects.admitTarget({ plan: copy(selected(plan, journal)), facts: copy(journal.evidence), operationKey: `${plan.transitionId}:admission` });
    const admission = await effects.verifyAdmission({ plan: copy(selected(plan, journal)), facts: copy(journal.evidence) });
    checkFact("admission", admission, plan); journal.evidence.admission = copy(admission); await persist("complete");
    return copy(journal);
  });
}
