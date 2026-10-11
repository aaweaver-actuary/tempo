import { z } from "zod";
import { PendingOperationError } from "./operation-status";
import { prefixTransitionApplySchema, prefixTransitionResultSchema, prefixTransitionSummarySchema,
  type PrefixTransitionRequest, type PrefixTransitionSummary } from "../domain/prefix-transition";

const storagePrefix = "tempo-pending-prefix-transition-v1:";
const journalLockName = `${storagePrefix}reservation`;
const recordSchema = z.strictObject({ version: z.literal(1), operationId: z.string().min(1), repertoireId: z.string().min(1),
  request: prefixTransitionApplySchema, summary: prefixTransitionSummarySchema,
  state: z.enum(["pending", "transport_unknown", "stale", "blocked", "failed", "published"]),
  message: z.string().optional(), transitionPhase: z.string().optional(), failureCode: z.string().optional(),
  result: prefixTransitionResultSchema.optional(), journalMissing: z.boolean().optional(),
});
export type PrefixTransitionOperation = z.infer<typeof recordSchema>;
export type PrefixTransitionRecoveryIssue = { code: "journal_missing" | "storage_unavailable" | "serialization_unavailable"; message: string };
const knownOperations = new Map<string, PrefixTransitionOperation>();
const recoveryIssues = new Map<string, PrefixTransitionRecoveryIssue>();
const listeners = new Set<() => void>();
const storageKey = (repertoireId: string) => storagePrefix + encodeURIComponent(repertoireId);
const errorMessage = (failure: unknown) => typeof failure === "object" && failure !== null && "message" in failure && typeof failure.message === "string" ? failure.message : "storage unavailable";
const lockUnavailableMessage = "Safe prefix transitions require Web Locks. Open Tempo in a current browser using HTTPS or localhost, then check the original operation. No new delivery was sent.";
const missingJournalMessage = "The browser recovery record disappeared. This session retains the original identity. Check status; do not confirm another transition. Recovery after a full page reload is not guaranteed.";
export const unresolvedPrefixTransition = (operation: PrefixTransitionOperation) => ["pending", "transport_unknown", "blocked"].includes(operation.state);
export const effectivePrefixTransitionScope = (operation: PrefixTransitionOperation) => [...new Set([operation.repertoireId, ...operation.summary.affectedRepertoireIds])];
export const prefixTransitionRecoveryIssue = (repertoireId: string) => recoveryIssues.get(repertoireId) ?? null;
export const knownPrefixTransitions = () => [...knownOperations.values()].map(operation => recordSchema.parse(operation));
export function resetPrefixTransitionSessionForTests() {
  if (import.meta.env.MODE !== "test") throw new Error("Session reset is restricted to tests");
  knownOperations.clear(); recoveryIssues.clear();
}
export function notifyPrefixTransitions() {
  for (const listener of listeners) { try { listener(); } catch { /* Observers cannot change command ownership. */ } }
}
export function subscribePrefixTransitions(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(storagePrefix)) listener(); };
  window.addEventListener("storage", changed);
  const journalObserver = window.setInterval(() => { if (knownOperations.size) listener(); }, 1000);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); window.clearInterval(journalObserver); };
}
function matchingIdentity(first: PrefixTransitionOperation, second: PrefixTransitionOperation) {
  return first.operationId === second.operationId && first.repertoireId === second.repertoireId
    && JSON.stringify(first.request) === JSON.stringify(second.request) && JSON.stringify(first.summary) === JSON.stringify(second.summary);
}
export function verifiedPrefixPublication(operation: PrefixTransitionOperation) {
  const result = operation.result;
  return operation.state === "published" && Boolean(result && result.operation_id === operation.operationId
    && result.plan_id === operation.request.plan_id && result.repertoire_id === operation.repertoireId
    && !result.no_op && result.queue_date && result.graph_generation > operation.request.graph_generation && operation.transitionPhase === "complete");
}
export function readPersistedPrefixTransition(repertoireId: string): PrefixTransitionOperation | null {
  let stored: string | null;
  try { stored = localStorage.getItem(storageKey(repertoireId)); }
  catch (failure) {
    recoveryIssues.set(repertoireId, { code: "storage_unavailable", message: failure instanceof Error ? failure.message : "Browser storage is unavailable. Keep the original recovery identity." });
    throw failure;
  }
  if (stored === null) return null;
  try {
    const operation = recordSchema.parse(JSON.parse(stored));
    if (operation.repertoireId !== repertoireId || !operation.summary.affectedRepertoireIds.includes(repertoireId)
      || JSON.stringify(operation.request.selected_line_ids) !== JSON.stringify(operation.summary.routes.map(route => route.id))) throw new Error("Mismatched scope");
    const known = knownOperations.get(repertoireId);
    if (known && !matchingIdentity(known, operation) && (known.operationId === operation.operationId || unresolvedPrefixTransition(known)))
      throw new Error("Changed ownership or immutable approved payload");
    const selectedRouteIds = operation.request.selected_line_ids;
    if (new Set(selectedRouteIds).size !== selectedRouteIds.length
      || JSON.stringify(Object.keys(operation.request.candidate_depths).sort()) !== JSON.stringify([...selectedRouteIds].sort())
      || operation.summary.routes.some(route => operation.request.candidate_depths[route.id] > route.saved_depth))
      throw new Error("Inconsistent approved route depths");
    if (operation.state === "published" && !verifiedPrefixPublication(operation)) {
      operation.state = "transport_unknown"; delete operation.result;
      operation.message = "The saved completion cannot verify this approved publication. Check status to recover the original operation.";
    }
    delete operation.journalMissing;
    return operation;
  } catch {
    const message = "The saved prefix transition is unreadable. Restore browser data before changing this repertoire; do not clear an unresolved operation.";
    recoveryIssues.set(repertoireId, { code: "storage_unavailable", message });
    throw new Error(message);
  }
}
export function readPrefixTransition(repertoireId: string): PrefixTransitionOperation | null {
  const persisted = readPersistedPrefixTransition(repertoireId);
  const known = knownOperations.get(repertoireId);
  if (!persisted) {
    if (!known) return null;
    // A resolved record may legitimately be cleared by another tab. Unresolved
    // identities remain guarded until status proves resolution in this session.
    if (verifiedPrefixPublication(known) && !recoveryIssues.has(repertoireId)) { knownOperations.delete(repertoireId); return null; }
    const missing = { ...known, journalMissing: true };
    knownOperations.set(repertoireId, missing);
    recoveryIssues.set(repertoireId, { code: "journal_missing", message: missingJournalMessage });
    return recordSchema.parse(missing);
  }
  const current = known && matchingIdentity(known, persisted) && verifiedPrefixPublication(known) ? known : persisted;
  knownOperations.set(repertoireId, current);
  if (!known || !matchingIdentity(known, persisted) || !verifiedPrefixPublication(known) || verifiedPrefixPublication(persisted)) recoveryIssues.delete(repertoireId);
  return recordSchema.parse(current);
}
export function savedPrefixTransitions(): PrefixTransitionOperation[] {
  const repertoireIds = new Set([...knownOperations.keys(), ...Object.keys(localStorage).filter(key => key.startsWith(storagePrefix))
    .map(key => decodeURIComponent(key.slice(storagePrefix.length)))]);
  return [...repertoireIds].sort().map(readPrefixTransition).filter((operation): operation is PrefixTransitionOperation => operation !== null);
}
export function hasPrefixTransitionJournalLocks() {
  try { return typeof navigator.locks?.request === "function"; } catch { return false; }
}
/** One feature-specific, short synchronous section. Never return a network promise. */
export async function withPrefixTransitionJournalLock<T>(signal: AbortSignal | undefined, criticalSection: () => T): Promise<T> {
  signal?.throwIfAborted();
  if (!hasPrefixTransitionJournalLocks()) throw new Error(lockUnavailableMessage);
  let enteredCriticalSection = false;
  try {
    return await navigator.locks.request(journalLockName, { signal }, () => {
      enteredCriticalSection = true; signal?.throwIfAborted(); return criticalSection();
    });
  } catch (failure) {
    if (enteredCriticalSection || signal?.aborted) throw failure;
    throw new Error(lockUnavailableMessage, { cause: failure });
  }
}
export class PrefixTransitionPersistenceError extends Error {}
export function assertPrefixTransitionOwnership(current: PrefixTransitionOperation | null, owner: PrefixTransitionOperation): asserts current is PrefixTransitionOperation {
  if (current?.operationId !== owner.operationId || current.repertoireId !== owner.repertoireId)
    throw new Error("The saved prefix transition changed. Reopen it to recover the current operation.");
  if (!matchingIdentity(current, owner)) throw new Error("The saved prefix transition payload changed. Recover its original browser data before delivery.");
}
/** Reconcile the proposed state with current persisted ownership inside the lock. */
export async function savePrefixTransitionOperation(proposed: PrefixTransitionOperation, memoryOnly = false, signal?: AbortSignal): Promise<PrefixTransitionOperation> {
  const reconcile = () => {
    let persisted: PrefixTransitionOperation | null = null;
    try { persisted = readPersistedPrefixTransition(proposed.repertoireId); }
    catch (failure) { if (!memoryOnly) throw failure; }
    const known = knownOperations.get(proposed.repertoireId);
    if (!persisted && !memoryOnly && known && matchingIdentity(known, proposed) && verifiedPrefixPublication(proposed)) {
      knownOperations.set(proposed.repertoireId, { ...proposed, journalMissing: true });
      recoveryIssues.set(proposed.repertoireId, { code: "journal_missing", message: missingJournalMessage });
      notifyPrefixTransitions();
    }
    assertPrefixTransitionOwnership(persisted ?? (memoryOnly ? known ?? null : null), proposed);
    const current = known && persisted && matchingIdentity(known, persisted) && verifiedPrefixPublication(known) ? known : persisted ?? known!;
    const reconciled = recordSchema.parse(verifiedPrefixPublication(current) ? current : proposed);
    if (memoryOnly) {
      reconciled.journalMissing = !persisted || undefined;
      if (!hasPrefixTransitionJournalLocks()) recoveryIssues.set(proposed.repertoireId, { code: "serialization_unavailable", message: lockUnavailableMessage });
      knownOperations.set(proposed.repertoireId, reconciled); notifyPrefixTransitions(); return recordSchema.parse(reconciled);
    }
    delete reconciled.journalMissing;
    try { localStorage.setItem(storageKey(proposed.repertoireId), JSON.stringify(reconciled)); }
    catch (failure) {
      knownOperations.set(proposed.repertoireId, reconciled);
      const message = `Browser recovery data could not be saved: ${errorMessage(failure)}. Keep this page open and the original operation identity.`;
      recoveryIssues.set(proposed.repertoireId, { code: "storage_unavailable", message }); notifyPrefixTransitions();
      throw new PrefixTransitionPersistenceError(message);
    }
    knownOperations.set(proposed.repertoireId, reconciled); recoveryIssues.delete(proposed.repertoireId); notifyPrefixTransitions();
    return recordSchema.parse(reconciled);
  };
  return memoryOnly ? reconcile() : withPrefixTransitionJournalLock(signal, reconcile);
}
export function beginPrefixTransition(repertoireId: string, request: PrefixTransitionRequest, summary: PrefixTransitionSummary, signal?: AbortSignal) {
  return reservePrefixTransition(repertoireId, request, summary, signal);
}
export function reservePrefixTransition(repertoireId: string, request: PrefixTransitionRequest, summary: PrefixTransitionSummary, signal?: AbortSignal): Promise<PrefixTransitionOperation> {
  return withPrefixTransitionJournalLock(signal, () => {
    const previous = readPrefixTransition(repertoireId);
    if (previous) {
      if (JSON.stringify(previous.request) !== JSON.stringify(request) || JSON.stringify(previous.summary) !== JSON.stringify(summary))
        throw new PendingOperationError(previous.operationId, "Resolve the saved prefix transition before changing its approved plan.");
      return previous;
    }
    const overlapping = savedPrefixTransitions().find(operation => unresolvedPrefixTransition(operation)
      && effectivePrefixTransitionScope(operation).some(id => [repertoireId, ...summary.affectedRepertoireIds].includes(id)));
    if (overlapping) throw new PendingOperationError(overlapping.operationId, "Resolve the prefix transition affecting this repertoire before confirming another.");
    if (!summary.affectedRepertoireIds.includes(repertoireId)) throw new Error("The approved scope omits its target repertoire.");
    const operation = recordSchema.parse({ version: 1, operationId: crypto.randomUUID(), repertoireId, request, summary, state: "pending" });
    // Remember an allocated identity even if browser persistence rejects it.
    knownOperations.set(repertoireId, operation);
    try { localStorage.setItem(storageKey(operation.repertoireId), JSON.stringify(operation)); }
    catch (failure) {
      recoveryIssues.set(repertoireId, { code: "storage_unavailable", message: `Recovery data could not be saved. Keep this page open; no delivery was sent. ${failure instanceof Error ? failure.message : ""}` });
      notifyPrefixTransitions(); throw failure;
    }
    notifyPrefixTransitions(); return recordSchema.parse(operation);
  });
}
export function clearResolvedPrefixTransition(repertoireId: string, operationId: string, signal?: AbortSignal): Promise<void> {
  return withPrefixTransitionJournalLock(signal, () => {
    const persisted = readPersistedPrefixTransition(repertoireId);
    const known = knownOperations.get(repertoireId);
    const operation = persisted ?? known;
    if (!operation || operation.operationId !== operationId || unresolvedPrefixTransition(operation)) return;
    if (persisted) localStorage.removeItem(storageKey(repertoireId));
    knownOperations.delete(repertoireId); recoveryIssues.delete(repertoireId); notifyPrefixTransitions();
  });
}

/** Explicit user restoration only; status recovery never calls this writer. */
export function restoreKnownPrefixTransition(repertoireId: string, operationId: string, signal: AbortSignal) {
  return withPrefixTransitionJournalLock(signal, () => {
    const known = knownOperations.get(repertoireId);
    if (!known || known.operationId !== operationId) throw new Error("The original recovery identity is unavailable.");
    if (localStorage.getItem(storageKey(repertoireId)) !== null) throw new Error("A recovery record already exists. Check status; restoration cannot overwrite browser data.");
    if (!unresolvedPrefixTransition(known)) return recordSchema.parse(known);
    const restored = recordSchema.parse(known); delete restored.journalMissing;
    try { localStorage.setItem(storageKey(restored.repertoireId), JSON.stringify(restored)); }
    catch (failure) {
      recoveryIssues.set(repertoireId, { code: "storage_unavailable", message: `Recovery data could not be restored: ${errorMessage(failure)}` });
      notifyPrefixTransitions(); throw failure;
    }
    knownOperations.set(repertoireId, restored); recoveryIssues.delete(repertoireId); notifyPrefixTransitions(); return recordSchema.parse(restored);
  });
}

export type StructuralMutationScope = { repertoireIds: readonly string[]; cardIds?: readonly string[]; scopeComplete: boolean };
export function readPrefixStructuralAdmission(scope: StructuralMutationScope): { paused: boolean; reason?: string } {
  if (!hasPrefixTransitionJournalLocks()) return { paused: true, reason: lockUnavailableMessage };
  try {
    const unresolved = savedPrefixTransitions().filter(unresolvedPrefixTransition);
    const conflict = unresolved.find(operation => !scope.scopeComplete || !scope.repertoireIds.length
      || effectivePrefixTransitionScope(operation).some(id => scope.repertoireIds.includes(id)));
    if (conflict) return { paused: true, reason: prefixTransitionRecoveryIssue(conflict.repertoireId)?.message ?? "Resolve the prefix transition affecting this repertoire before changing its structure." };
    return { paused: false };
  } catch (failure) { return { paused: true, reason: failure instanceof Error ? failure.message : "Prefix recovery scope is unavailable." }; }
}
export async function admitPrefixStructuralMutation<T>(scope: StructuralMutationScope, dispatch: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  const dispatched = await withPrefixTransitionJournalLock(signal, () => {
    const admission = readPrefixStructuralAdmission(scope);
    if (admission.paused) throw new Error(admission.reason);
    // The wrapper prevents Web Locks from awaiting the network response.
    return { response: dispatch() };
  });
  return dispatched.response;
}
