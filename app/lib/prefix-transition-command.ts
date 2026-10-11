import { z } from "zod";
import { API_URL } from "../const";
import { backgroundReadWhenAdmitted } from "./background-fetch";
import { prefixTransitionReceiptSchema, prefixTransitionResultSchema, type PrefixTransitionReceipt } from "../domain/prefix-transition";
import { readPrefixTransition, readPersistedPrefixTransition, unresolvedPrefixTransition,
  savePrefixTransitionOperation, hasPrefixTransitionJournalLocks,
  restoreKnownPrefixTransition, knownPrefixTransitions, PrefixTransitionPersistenceError, PrefixTransitionSerializationError,
  assertPrefixTransitionOwnership, type PrefixTransitionOperation } from "./prefix-transition-journal";
export * from "./prefix-transition-journal";

/** User-requested restore checks server status first and never overwrites ownership. */
export async function restorePrefixTransitionJournal(repertoireId: string, operationId: string, signal: AbortSignal) {
  if (!hasPrefixTransitionJournalLocks()) throw new Error("Restoring recovery records requires browser Web Locks. Keep this identity and use a current browser on HTTPS or localhost.");
  const known = readPrefixTransition(repertoireId);
  if (!known || known.operationId !== operationId) throw new Error("The original recovery identity is unavailable.");
  const checked = await recoverPrefixTransition(repertoireId, { signal });
  if (checked && !unresolvedPrefixTransition(checked)) return checked;
  return restoreKnownPrefixTransition(repertoireId, operationId, signal);
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
  let memoryOnly = !hasPrefixTransitionJournalLocks();
  let operation: PrefixTransitionOperation | null;
  try { operation = readPrefixTransition(repertoireId); }
  catch (failure) {
    operation = knownPrefixTransitions().find(known => known.repertoireId === repertoireId) ?? null;
    if (!operation) throw failure;
    memoryOnly = true;
  }
  memoryOnly ||= Boolean(operation?.journalMissing);
  if (!operation) return null;
  const originalId = operation.operationId;
  const deadline = AbortSignal.timeout(30_000);
  const signal = AbortSignal.any([options.signal, deadline]);
  let posted = false;
  let retryRequested = false;
  let retryAdmission: "accepted" | "rejected" | "unknown" | null = null;
  let retryFeedback = "";
  const reconcile = async (proposed: PrefixTransitionOperation, writeSignal: AbortSignal) => {
    try { operation = await savePrefixTransitionOperation(proposed, memoryOnly, writeSignal); }
    catch (failure) {
      if (!(failure instanceof PrefixTransitionSerializationError)) throw failure;
      memoryOnly = true; operation = await savePrefixTransitionOperation(proposed, true, writeSignal);
    }
    return operation;
  };
  const update = (changes: Partial<PrefixTransitionOperation>) => {
    signal.throwIfAborted(); return reconcile({ ...operation!, ...changes }, signal);
  };
  try {
    for (;;) {
      signal.throwIfAborted();
      if (!memoryOnly) {
        const latest = readPersistedPrefixTransition(repertoireId);
        assertPrefixTransitionOwnership(latest, operation);
      }
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
        const blocked = await update({ state: "blocked", transitionPhase: receipt.transition?.state, failureCode: failure.code,
          message: receipt.transition?.recovery ?? failure.message ?? receipt.message ?? "Publication needs recovery. Resolve the reported service error and retry this operation." });
        if (blocked.state === "published") return blocked;
        if (options.retryBlocked && !retryRequested && !memoryOnly) {
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
        if (retryAdmission === "unknown") await update({ message: retryFeedback });
      } else if (receipt.state === "unknown") {
        await update({ state: "transport_unknown", message: "Delivery is unconfirmed. Recovery uses the same operation and approved plan." });
        if (operation!.state === "published" || posted || retryRequested || memoryOnly) return operation;
        posted = true;
        const delivery = readPersistedPrefixTransition(repertoireId);
        if (delivery?.journalMissing || delivery?.operationId !== originalId || JSON.stringify(delivery.request) !== JSON.stringify(operation!.request))
          throw new Error("The saved prefix transition changed before delivery. Reopen recovery.");
        if (delivery.state === "published") return delivery;
        // Neither a 200 nor a 202 response is completion evidence here.
        try { await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/prefix-transition/apply`, {
          method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": originalId }, body: JSON.stringify(delivery.request),
        }); } catch { signal.throwIfAborted(); }
        continue;
      } else await update({ state: "pending", transitionPhase: receipt.transition?.state,
        message: receipt.transition?.state === "publishing" ? "Waiting for graph, integrity and queue publication." : "The approved prefix transition is in progress." });
      if (operation!.state === "published") return operation;
      await pause(signal);
    }
  } catch (failure) {
    if (options.signal.aborted) throw options.signal.reason;
    if (failure instanceof PrefixTransitionPersistenceError) throw failure;
    if (!memoryOnly) assertPrefixTransitionOwnership(readPersistedPrefixTransition(repertoireId), operation!);
    if (deadline.aborted) return reconcile({ ...operation!,
      message: "Status checking timed out after 30 seconds. Backend work may still be running. Check status again using the original operation." }, options.signal);
    return reconcile({ ...operation!, state: operation!.state === "blocked" ? "blocked" : "transport_unknown",
      message: failure instanceof Error ? failure.message : "Connection unavailable. Check status to recover the same operation." }, options.signal);
  }
}
