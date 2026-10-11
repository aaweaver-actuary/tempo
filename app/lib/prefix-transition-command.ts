import { z } from "zod";
import { API_URL } from "../const";
import { backgroundReadWhenAdmitted } from "./background-fetch";
import { PendingOperationError } from "./operation-status";
import { prefixTransitionApplySchema, prefixTransitionReceiptSchema, prefixTransitionResultSchema,
  prefixTransitionSummarySchema, type PrefixTransitionRequest, type PrefixTransitionSummary } from "../domain/prefix-transition";

const storagePrefix = "tempo-pending-prefix-transition-v1:";
const recordSchema = z.strictObject({ version: z.literal(1), operationId: z.string().min(1), repertoireId: z.string().min(1),
  request: prefixTransitionApplySchema, summary: prefixTransitionSummarySchema,
  state: z.enum(["pending", "transport_unknown", "stale", "blocked", "failed", "published"]),
  message: z.string().optional(), transitionPhase: z.string().optional(), failureCode: z.string().optional(),
  result: prefixTransitionResultSchema.optional(),
});
export type PrefixTransitionOperation = z.infer<typeof recordSchema>;
const listeners = new Set<() => void>();
export const unresolvedPrefixTransition = (operation: PrefixTransitionOperation) =>
  ["pending", "transport_unknown", "blocked"].includes(operation.state);
const storageKey = (repertoireId: string) => storagePrefix + encodeURIComponent(repertoireId);
function notify() { for (const listener of listeners) { try { listener(); } catch { /* Observers cannot change command ownership. */ } } }
export function subscribePrefixTransitions(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(storagePrefix)) listener(); };
  window.addEventListener("storage", changed);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); };
}
export function readPrefixTransition(repertoireId: string): PrefixTransitionOperation | null {
  const stored = localStorage.getItem(storageKey(repertoireId));
  if (!stored) return null;
  try {
    const operation = recordSchema.parse(JSON.parse(stored));
    if (operation.repertoireId !== repertoireId || JSON.stringify(operation.request.selected_line_ids)
      !== JSON.stringify(operation.summary.routes.map(route => route.id))) throw new Error("Mismatched scope");
    return operation;
  } catch { throw new Error("The saved prefix transition is unreadable. Restore browser data before changing this repertoire; do not clear an unresolved operation."); }
}
export function savedPrefixTransitions(): PrefixTransitionOperation[] {
  return Object.keys(localStorage).filter(key => key.startsWith(storagePrefix)).sort()
    .map(key => readPrefixTransition(decodeURIComponent(key.slice(storagePrefix.length))))
    .filter((operation): operation is PrefixTransitionOperation => operation !== null);
}
function saveOperation(operation: PrefixTransitionOperation, creating = false): PrefixTransitionOperation {
  const previous = readPrefixTransition(operation.repertoireId);
  if (!creating && previous?.operationId !== operation.operationId)
    throw new Error("The saved prefix transition changed. Reopen it to recover the current operation.");
  if (creating && previous) throw new PendingOperationError(previous.operationId, "Resolve the saved prefix transition before confirming another.");
  const validated = recordSchema.parse(operation);
  localStorage.setItem(storageKey(operation.repertoireId), JSON.stringify(validated));
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
    && operation.summary.affectedRepertoireIds.includes(repertoireId));
  if (overlapping) throw new PendingOperationError(overlapping.operationId, "Resolve the prefix transition affecting this repertoire before confirming another.");
  return saveOperation({ version: 1, operationId: crypto.randomUUID(), repertoireId,
    request, summary, state: "pending" }, true);
}
export async function reservePrefixTransition(repertoireId: string, request: PrefixTransitionRequest, summary: PrefixTransitionSummary, signal: AbortSignal) {
  const reserve = () => { signal.throwIfAborted(); return beginPrefixTransition(repertoireId, request, summary); };
  // Serialize only this feature's journal reservation across browser tabs.
  return navigator.locks ? navigator.locks.request(storageKey(repertoireId), { signal }, reserve) : reserve();
}
export function clearResolvedPrefixTransition(repertoireId: string, operationId: string) {
  const operation = readPrefixTransition(repertoireId);
  if (operation?.operationId !== operationId || unresolvedPrefixTransition(operation)) return;
  localStorage.removeItem(storageKey(repertoireId)); notify();
}

function pause(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cancel = () => { clearTimeout(timer); signal.removeEventListener("abort", cancel); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", cancel); resolve(); }, 1000);
    signal.addEventListener("abort", cancel, { once: true });
    if (signal.aborted) cancel();
  });
}
/** Resolve only this frozen delivery. Unknown admissions may replay once per recovery action. */
export async function recoverPrefixTransition(repertoireId: string, options: { signal: AbortSignal; retryBlocked?: boolean }): Promise<PrefixTransitionOperation | null> {
  let operation = readPrefixTransition(repertoireId);
  if (!operation || !unresolvedPrefixTransition(operation)) return operation;
  const originalId = operation.operationId;
  const signal = AbortSignal.any([options.signal, AbortSignal.timeout(30_000)]);
  let posted = false;
  let retryRequested = false;
  const update = (changes: Partial<PrefixTransitionOperation>) => {
    signal.throwIfAborted();
    operation = saveOperation({ ...operation!, ...changes });
    return operation;
  };
  try {
    if (options.retryBlocked && operation.state === "blocked") {
      retryRequested = true;
      try { await fetch(`${API_URL}/api/operations/${encodeURIComponent(originalId)}/retry`, { method: "POST", signal }); }
      catch { signal.throwIfAborted(); /* Retry acknowledgement cannot establish the outcome. */ }
    }
    for (;;) {
      signal.throwIfAborted();
      if (readPrefixTransition(repertoireId)?.operationId !== originalId) throw new Error("Prefix transition ownership changed. Reopen recovery.");
      const status = await backgroundReadWhenAdmitted(`${API_URL}/api/operations/${encodeURIComponent(originalId)}`, signal);
      const receipt = status.status === 404 ? { operation_id: originalId, state: "unknown" as const }
        : status.ok ? prefixTransitionReceiptSchema.parse(await status.json()) : null;
      signal.throwIfAborted();
      if (!receipt || receipt.operation_id !== originalId) throw new Error("The service could not verify this exact prefix operation. Check status again.");
      if (receipt.state === "complete") {
        const result = prefixTransitionResultSchema.parse(receipt.response);
        if (result.operation_id !== originalId || result.plan_id !== operation.request.plan_id || result.repertoire_id !== repertoireId
          || result.no_op || !result.queue_date || result.graph_generation <= operation.request.graph_generation
          || receipt.transition?.state !== "complete" || receipt.transition.graph_generation !== result.graph_generation
          || receipt.transition.queue_date !== result.queue_date)
          throw new Error("The completion receipt does not verify the approved graph and queue publication. Keep this identity and check status again.");
        return update({ state: "published", result, message: "Prefix transition published. Graph, integrity and daily queue are confirmed.", transitionPhase: "complete" });
      }
      if (receipt.state === "failed") {
        const detail = z.object({ code: z.string().optional(), message: z.string().optional() }).safeParse(receipt.error?.detail);
        const code = receipt.error?.code ?? (detail.success ? detail.data.code : undefined);
        return update({ state: code === "stale_plan" || code === "stale_snapshot" ? "stale" : "failed", failureCode: code,
          message: (detail.success ? detail.data.message : undefined) ?? receipt.error?.message ?? "The operation failed. Review a fresh comparison before trying again.",
          transitionPhase: receipt.transition?.state });
      }
      if (receipt.state === "blocked") {
        const blocked = update({ state: "blocked", transitionPhase: receipt.transition?.state,
          message: receipt.transition?.recovery ?? receipt.last_error?.message ?? receipt.message ?? "Publication needs recovery. Resolve the reported service error and retry this operation." });
        if (!retryRequested) return blocked;
      } else if (receipt.state === "unknown") {
        update({ state: "transport_unknown", message: "Delivery is unconfirmed. Recovery uses the same operation and approved plan." });
        if (posted || retryRequested) return operation;
        posted = true;
        // Neither a 200 nor a 202 response is completion evidence here.
        try { await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/prefix-transition/apply`, {
          method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": originalId }, body: JSON.stringify(operation!.request),
        }); } catch { signal.throwIfAborted(); }
        continue;
      } else update({ state: "pending", transitionPhase: receipt.transition?.state,
        message: receipt.transition?.state === "publishing" ? "Waiting for graph, integrity and queue publication." : "The approved prefix transition is in progress." });
      await pause(signal);
    }
  } catch (failure) {
    if (options.signal.aborted) throw options.signal.reason;
    if (readPrefixTransition(repertoireId)?.operationId !== originalId) throw failure;
    if (signal.aborted) return operation;
    return saveOperation({ ...operation!, state: "transport_unknown",
      message: failure instanceof Error ? failure.message : "Connection unavailable. Check status to recover the same operation." });
  }
}
