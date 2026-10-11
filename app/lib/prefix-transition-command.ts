import { z } from "zod";
import { API_URL } from "../const";
import { backgroundReadWhenAdmitted } from "./background-fetch";
import { PendingOperationError } from "./operation-status";
import { prefixTransitionApplySchema, prefixTransitionReceiptSchema, prefixTransitionResultSchema,
  prefixTransitionSummarySchema, type PrefixTransitionReceipt, type PrefixTransitionRequest, type PrefixTransitionSummary } from "../domain/prefix-transition";

const storagePrefix = "tempo-pending-prefix-transition-v1:";
const recordSchema = z.strictObject({ version: z.literal(1), operationId: z.string().min(1), repertoireId: z.string().min(1),
  request: prefixTransitionApplySchema, summary: prefixTransitionSummarySchema,
  state: z.enum(["pending", "transport_unknown", "stale", "blocked", "failed", "published"]),
  message: z.string().optional(), transitionPhase: z.string().optional(), failureCode: z.string().optional(),
  result: prefixTransitionResultSchema.optional(),
  journalMissing: z.boolean().optional(),
});
export type PrefixTransitionOperation = z.infer<typeof recordSchema>;
const listeners = new Set<() => void>();
// Session ownership survives component lifetimes, but cannot survive process restart.
const knownOperations = new Map<string, PrefixTransitionOperation>();
const failedJournalWrites = new Set<string>();
export const knownPrefixTransitions = () => [...knownOperations.values()];
export function resetPrefixTransitionSessionForTests() {
  if (import.meta.env.MODE !== "test") throw new Error("Session reset is restricted to tests");
  knownOperations.clear(); failedJournalWrites.clear();
}
export const effectivePrefixTransitionScope = (operation: PrefixTransitionOperation) =>
  [...new Set([operation.repertoireId, ...operation.summary.affectedRepertoireIds])];
const missingJournalMessage = "The browser recovery record disappeared. The original operation is still known in this session. Check status or restore the saved recovery record; do not confirm another transition.";

export const unresolvedPrefixTransition = (operation: PrefixTransitionOperation) =>
  ["pending", "transport_unknown", "blocked"].includes(operation.state);
const storageKey = (repertoireId: string) => storagePrefix + encodeURIComponent(repertoireId);
function notify() { for (const listener of listeners) { try { listener(); } catch { /* Observers cannot change command ownership. */ } } }
export function subscribePrefixTransitions(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(storagePrefix)) listener(); };
  window.addEventListener("storage", changed);
  // Same-tab removeItem/clear calls emit no storage event. Only known ownership needs observation.
  const journalObserver = window.setInterval(() => { if (knownOperations.size) listener(); }, 1000);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); window.clearInterval(journalObserver); };
}
export function readPrefixTransition(repertoireId: string): PrefixTransitionOperation | null {
  const stored = localStorage.getItem(storageKey(repertoireId));
  if (stored === null) {
    const known = knownOperations.get(repertoireId);
    if (!known) return null;
    const missing = { ...known, journalMissing: true, message: known.journalMissing ? known.message : missingJournalMessage };
    knownOperations.set(repertoireId, missing);
    return missing;
  }
  try {
    const operation = recordSchema.parse(JSON.parse(stored));
    if (!operation.summary.affectedRepertoireIds.includes(repertoireId) || operation.repertoireId !== repertoireId || JSON.stringify(operation.request.selected_line_ids)
      !== JSON.stringify(operation.summary.routes.map(route => route.id))) throw new Error("Mismatched scope");
    const selectedRouteIds = operation.request.selected_line_ids;
    if (new Set(selectedRouteIds).size !== selectedRouteIds.length
      || JSON.stringify(Object.keys(operation.request.candidate_depths).sort()) !== JSON.stringify([...selectedRouteIds].sort())
      || operation.summary.routes.some(route => operation.request.candidate_depths[route.id] > route.saved_depth))
      throw new Error("Inconsistent approved route depths");
    const known = knownOperations.get(repertoireId);
    if (known && (known.operationId !== operation.operationId || JSON.stringify(known.request) !== JSON.stringify(operation.request)
      || JSON.stringify(known.summary) !== JSON.stringify(operation.summary)))
      throw new Error("Changed ownership or approved payload");
    const result = operation.result;
    if (operation.state === "published" && (!result || result.operation_id !== operation.operationId
      || result.plan_id !== operation.request.plan_id || result.repertoire_id !== repertoireId
      || result.no_op || !result.queue_date || result.graph_generation <= operation.request.graph_generation
      || operation.transitionPhase !== "complete"))
    {
      operation.state = "transport_unknown";
      operation.result = undefined;
      operation.message = "The saved completion cannot verify this approved publication. Check status to recover the original operation.";
    }
    if (known && failedJournalWrites.has(repertoireId)) return known;
    delete operation.journalMissing;
    knownOperations.set(repertoireId, operation);
    return operation;
  } catch { throw new Error("The saved prefix transition is unreadable. Restore browser data before changing this repertoire; do not clear an unresolved operation."); }
}
export function savedPrefixTransitions(): PrefixTransitionOperation[] {
  return [...new Set([...knownOperations.keys(), ...Object.keys(localStorage).filter(key => key.startsWith(storagePrefix))
    .map(key => decodeURIComponent(key.slice(storagePrefix.length)))])].sort()
    .map(repertoireId => readPrefixTransition(repertoireId))
    .filter((operation): operation is PrefixTransitionOperation => operation !== null);
}
function saveOperation(operation: PrefixTransitionOperation, creating = false, statusOnly = false): PrefixTransitionOperation {
  const previous = readPrefixTransition(operation.repertoireId);
  if (!creating && previous?.operationId !== operation.operationId)
    throw new Error("The saved prefix transition changed. Reopen it to recover the current operation.");
  if (previous && (JSON.stringify(previous.request) !== JSON.stringify(operation.request)
    || JSON.stringify(previous.summary) !== JSON.stringify(operation.summary)))
    throw new Error("The saved prefix transition payload changed. Recover its original browser data before delivery.");
  if (creating && previous) throw new PendingOperationError(previous.operationId, "Resolve the saved prefix transition before confirming another.");
  const validated = recordSchema.parse(operation);
  if (previous?.journalMissing) {
    if (!statusOnly) throw new Error("The saved prefix transition changed. Restore its original recovery record before delivery.");
    validated.journalMissing = true;
  } else {
    delete validated.journalMissing;
    try { localStorage.setItem(storageKey(operation.repertoireId), JSON.stringify(validated)); failedJournalWrites.delete(operation.repertoireId); }
    catch (failure) {
      if (!creating) { knownOperations.set(operation.repertoireId, validated); failedJournalWrites.add(operation.repertoireId); notify(); }
      throw failure;
    }
  }
  knownOperations.set(operation.repertoireId, validated);
  notify();
  return validated;
}
export function beginPrefixTransition(repertoireId: string, request: PrefixTransitionRequest, summary: PrefixTransitionSummary): PrefixTransitionOperation {
  const previous = readPrefixTransition(repertoireId);
  if (previous) {
    if (JSON.stringify(previous.request) !== JSON.stringify(request))
      throw new PendingOperationError(previous.operationId, "Resolve the saved prefix transition before changing its approved plan.");
    return previous;
  }
  const overlapping = savedPrefixTransitions().find(operation => unresolvedPrefixTransition(operation)
    && effectivePrefixTransitionScope(operation).some(id => [repertoireId, ...summary.affectedRepertoireIds].includes(id)));
  if (overlapping) throw new PendingOperationError(overlapping.operationId, "Resolve the prefix transition affecting this repertoire before confirming another.");
  if (!summary.affectedRepertoireIds.includes(repertoireId)) throw new Error("The approved scope omits its target repertoire.");
  return saveOperation({ version: 1, operationId: crypto.randomUUID(), repertoireId,
    request, summary, state: "pending" }, true);
}
export async function reservePrefixTransition(repertoireId: string, request: PrefixTransitionRequest, summary: PrefixTransitionSummary, signal: AbortSignal) {
  const reserve = () => { signal.throwIfAborted(); return beginPrefixTransition(repertoireId, request, summary); };
  // Serialize only this feature's journal reservation across browser tabs.
  return navigator.locks ? navigator.locks.request(`${storagePrefix}reservation`, { signal }, reserve) : reserve();
}
export function clearResolvedPrefixTransition(repertoireId: string, operationId: string) {
  const operation = readPrefixTransition(repertoireId);
  if (operation?.operationId !== operationId || unresolvedPrefixTransition(operation)) return;
  localStorage.removeItem(storageKey(repertoireId)); knownOperations.delete(repertoireId); failedJournalWrites.delete(repertoireId); notify();
}

/** Restore only the exact approved record retained by this session. No delivery occurs. */
export async function restorePrefixTransitionJournal(repertoireId: string, operationId: string, signal: AbortSignal) {
  if (!navigator.locks) throw new Error("Restoring recovery records requires browser Web Locks. Keep this identity and use a supported browser to restore the original browser data.");
  // Reconcile deliberate clearing in another tab against server evidence before restoration.
  const checked = await recoverPrefixTransition(repertoireId, { signal });
  if (checked && !unresolvedPrefixTransition(checked)) return checked;
  return navigator.locks.request(`${storagePrefix}reservation`, { signal }, () => {
    signal.throwIfAborted();
    const known = knownOperations.get(repertoireId);
    if (!known || known.operationId !== operationId) throw new Error("The original recovery identity is unavailable.");
    const stored = localStorage.getItem(storageKey(repertoireId));
    if (stored !== null) throw new Error("A recovery record already exists. Check status; restoration cannot overwrite browser data.");
    const restored = recordSchema.parse({ ...known, journalMissing: undefined });
    localStorage.setItem(storageKey(repertoireId), JSON.stringify(restored));
    knownOperations.set(repertoireId, restored); failedJournalWrites.delete(repertoireId); notify();
    return restored;
  });
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); signal.removeEventListener("abort", cancel); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, 1000);
    signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) cancel();
  });
}
function operationFailure(error: PrefixTransitionReceipt["error"]) {
  const detail = z.object({ code: z.string().optional(), message: z.string().optional() }).safeParse(error?.detail);
  return { code: error?.code ?? (detail.success ? detail.data.code : undefined),
    message: (detail.success ? detail.data.message : undefined) ?? error?.message };
}
/** Resolve only this frozen delivery. Unknown admissions may replay once per recovery action. */
export async function recoverPrefixTransition(repertoireId: string, options: { signal: AbortSignal; retryBlocked?: boolean }): Promise<PrefixTransitionOperation | null> {
  let operation = readPrefixTransition(repertoireId);
  if (!operation || !unresolvedPrefixTransition(operation)) return operation;
  const originalId = operation.operationId;
  const deadline = AbortSignal.timeout(30_000);
  const signal = AbortSignal.any([options.signal, deadline]);
  let posted = false;
  let retryRequested = false;
  let retryAdmission: "accepted" | "rejected" | "unknown" | null = null;
  let retryFeedback = "";
  const update = (changes: Partial<PrefixTransitionOperation>) => {
    signal.throwIfAborted();
    operation = saveOperation({ ...operation!, ...changes }, false, true);
    return operation;
  };
  try {
    for (;;) {
      signal.throwIfAborted();
      if (readPrefixTransition(repertoireId)?.operationId !== originalId) throw new Error("Prefix transition ownership changed. Reopen recovery.");
      const status = await backgroundReadWhenAdmitted(`${API_URL}/api/operations/${encodeURIComponent(originalId)}`, signal);
      if (status.status === 404) throw new Error("Operation status is unavailable. Check the PostgreSQL service and check status again with the original identity.");
      const receiptPayload: unknown = status.status === 200 ? await status.json().catch(() => null) : null;
      const parsedReceipt = prefixTransitionReceiptSchema.safeParse(receiptPayload);
      const receipt = parsedReceipt.success ? parsedReceipt.data : null;
      signal.throwIfAborted();
      if (!receipt || receipt.operation_id !== originalId) throw new Error("The service could not verify this exact prefix operation. Check status again.");
      if (receipt.state === "complete") {
        const parsedResult = prefixTransitionResultSchema.safeParse(receipt.response);
        if (!parsedResult.success) throw new Error("The service returned an unreadable completion result. Keep this identity and check status again.");
        const result = parsedResult.data;
        if (result.operation_id !== originalId || result.plan_id !== operation.request.plan_id || result.repertoire_id !== repertoireId
          || result.no_op || !result.queue_date || result.graph_generation <= operation.request.graph_generation
          || receipt.transition?.state !== "complete" || receipt.transition.graph_generation !== result.graph_generation
          || receipt.transition.queue_date !== result.queue_date)
          throw new Error("The completion receipt does not verify the approved graph and queue publication. Keep this identity and check status again.");
        return update({ state: "published", result, message: "Prefix transition published. Graph, integrity and daily queue are confirmed.", transitionPhase: "complete" });
      }
      if (receipt.state === "failed") {
        const { code, message } = operationFailure(receipt.error);
        return update({ state: code === "stale_plan" || code === "stale_snapshot" ? "stale" : "failed", failureCode: code,
          message: message ?? "The operation failed. Review a fresh comparison before trying again.",
          transitionPhase: receipt.transition?.state });
      }
      if (receipt.state === "blocked") {
        const failure = operationFailure(receipt.last_error ?? receipt.error);
        const blocked = update({ state: "blocked", transitionPhase: receipt.transition?.state, failureCode: failure.code,
          message: receipt.transition?.recovery ?? failure.message ?? receipt.message ?? "Publication needs recovery. Resolve the reported service error and retry this operation." });
        if (options.retryBlocked && !retryRequested) {
          retryRequested = true;
          try {
            const response = await fetch(`${API_URL}/api/operations/${encodeURIComponent(originalId)}/retry`, { method: "POST", signal });
            signal.throwIfAborted();
            if (response.status === 202) retryAdmission = "accepted";
            else {
              retryAdmission = "rejected";
              const detail: unknown = await response.json().catch(() => null);
              const parsed = z.object({ detail: z.string() }).safeParse(detail);
              retryFeedback = `Retry was not admitted (HTTP ${response.status}). ${parsed.success ? parsed.data.detail : "Check the service and retry the original operation."}`;
            }
          } catch { signal.throwIfAborted(); retryAdmission = "unknown";
            retryFeedback = "The retry acknowledgment was lost. Backend work may still be running. Check status again using the original operation."; }
          continue; // Admission response is never publication evidence.
        }
        if (!retryRequested) return blocked;
        if (retryAdmission === "rejected") return update({ message: `${retryFeedback} The operation remains blocked; restore the service before retrying.` });
        if (retryAdmission === "unknown") update({ message: retryFeedback });
      } else if (receipt.state === "unknown") {
        update({ state: "transport_unknown", message: "Delivery is unconfirmed. Recovery uses the same operation and approved plan." });
        if (posted || retryRequested || operation.journalMissing) return operation;
        posted = true;
        const delivery = readPrefixTransition(repertoireId);
        if (delivery?.journalMissing || delivery?.operationId !== originalId || JSON.stringify(delivery.request) !== JSON.stringify(operation!.request))
          throw new Error("The saved prefix transition changed before delivery. Reopen recovery.");
        // Neither a 200 nor a 202 response is completion evidence here.
        try { await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/prefix-transition/apply`, {
          method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": originalId }, body: JSON.stringify(delivery.request),
        }); } catch { signal.throwIfAborted(); }
        continue;
      } else update({ state: "pending", transitionPhase: receipt.transition?.state,
        message: receipt.transition?.state === "publishing" ? "Waiting for graph, integrity and queue publication." : "The approved prefix transition is in progress." });
      await pause(signal);
    }
  } catch (failure) {
    if (options.signal.aborted) throw options.signal.reason;
    if (failedJournalWrites.has(repertoireId)) throw failure;
    if (readPrefixTransition(repertoireId)?.operationId !== originalId) throw failure;
    if (deadline.aborted) return saveOperation({ ...operation!,
      message: "Status checking timed out after 30 seconds. Backend work may still be running. Check status again using the original operation." }, false, true);
    return saveOperation({ ...operation!, state: operation!.state === "blocked" ? "blocked" : "transport_unknown",
      message: failure instanceof Error ? failure.message : "Connection unavailable. Check status to recover the same operation." }, false, true);
  }
}
