import { defaultStudyClient, type StudyClient } from "./study-connection";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import { notifications, publishNotification, resolveNotification } from "./notifications";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { isStudyIntentStoreCurrent, studyIntentStore } from "../../database/browser/study-intent-storage";
import { createStudyCommandDelivery as createDelivery, predecessorSettled, commandKinds, isRetiredCommand, requireRetainedCommand, StudyRequestRejectedError, type StudyCommandKind, type FrozenStudyRequest, type RejectionReceipt } from "./study-command-delivery";
export { StudyRequestRejectedError } from "./study-command-delivery";
export function createStudyCommandDelivery(client: StudyClient, getStore: () => Promise<IntentStore>, isStoreCurrent: (store: IntentStore) => boolean = () => true) {
  return createDelivery(client, getStore, { confirmOriginalOperationResponse, readOperationResponse, FailedOperationError, PendingOperationError }, isStoreCurrent);
}

const defaultDelivery = createStudyCommandDelivery(defaultStudyClient, studyIntentStore, isStudyIntentStoreCurrent);
for (const kind of commandKinds) registerStudyDeliveryAdapter(kind, defaultDelivery.deliver, defaultDelivery.checkReceipt);

function practiceScope(scope: string) { return scope.endsWith(":self-assess") ? scope.slice(0, -12) : scope; }
function boundaryKey(scope: string) { return `practice-reset:${practiceScope(scope)}`; }
function scopeSelector(scope: string) { const parent = practiceScope(scope); return (record: IntentRecord) => [parent, `${parent}:self-assess`].includes((record.payload as FrozenStudyRequest)?.scope); }
export async function studyPracticeBoundary(scope: string) {
  return (await (await studyIntentStore()).boundary(boundaryKey(scope), scopeSelector(scope))).value;
}
export async function executeStudyRequest(input: { scope: string; endpoint: string; body: Record<string, unknown>; requestId: string; attemptId?: string; revision?: number; kind?: StudyCommandKind; practiceBoundary?: number }): Promise<Response> {
  requireRetainedCommand(input.kind ?? "study-command");
  const store = await studyIntentStore();
  const key = `${input.scope}:${input.requestId}`;
  const previous = await store.get(key);
  const coordinator = await studySession();
  if (coordinator.getSnapshot().key !== key) coordinator.start(key);
  const boundary = input.practiceBoundary ?? await studyPracticeBoundary(input.scope);
  const result = await coordinator.submit({ key, kind: input.kind ?? "study-command", attemptId: input.attemptId ?? String(input.body.attempt_id ?? input.requestId),
    requestId: input.requestId, endpoint: input.endpoint, revision: input.revision ?? Number(input.body.revision ?? input.body.exercise_revision ?? 1),
    createdAt: previous?.createdAt ?? new Date().toISOString(), payload: { body: input.body, scope: input.scope,
      headers: { "Content-Type": "application/json", "Idempotency-Key": input.requestId } } }, undefined, { boundary: { key: boundaryKey(input.scope), value: boundary } });
  if (result.state === "discarded") {
    const receipt = result.receipt as RejectionReceipt | undefined;
    if (receipt?.type === "rejected") throw new StudyRequestRejectedError(receipt);
    throw new Error("This study request was explicitly discarded.");
  }
  if (!result.receipt) throw new PendingOperationError(input.requestId);
  return Response.json(result.receipt);
}

/** Restore evidence without deriving another payload from fresh UI state. Rejections are inspectable but never replayed. */
export async function retainedStudyRequest(scope: string, parentAttemptId?: string) {
  const snapshot = await (await studyIntentStore()).boundary(boundaryKey(scope), scopeSelector(scope));
  const retained = snapshot.records.filter(record => commandKinds.includes(record.kind as StudyCommandKind) &&
    (record.payload as FrozenStudyRequest).scope === scope && record.sequence > snapshot.value &&
    (!parentAttemptId || record.attemptId === parentAttemptId) && (record.state !== "discarded" || (record.receipt as RejectionReceipt)?.type === "rejected")).at(-1);
  return retained ? { requestId: retained.requestId, attemptId: retained.attemptId, revision: retained.revision, body: (retained.payload as FrozenStudyRequest).body,
    receipt: retained.receipt, state: retained.state, practiceBoundary: snapshot.value } : undefined;
}


/** Find only an unambiguous saved self-assessment parent after a failed presentation read. */
export async function retainedPendingStudyPresentation(studyId: string, exerciseId: string, endpoint: string,
  queue?: { cardId: string; entryId: number; cycle: number; revision: number }): Promise<string | undefined> {
  const store = await studyIntentStore();
  const records = await store.list("study-command");
  if (!isStudyIntentStoreCurrent(store)) throw new Error("Study authority changed while locating the original attempt.");
  const prefix = `study:${studyId}:${exerciseId}:${queue?.entryId ?? "practice"}:`;
  const candidates: string[] = [];
  for (const record of records) {
    const saved = record.payload as FrozenStudyRequest | undefined;
    const body = saved?.body;
    const receipt = record.receipt as { attempt_id?: unknown; pending_self_assessment?: unknown } | undefined;
    if (record.endpoint !== endpoint || record.state !== "committed" || !saved?.scope?.startsWith(prefix) ||
        saved.scope !== `${prefix}${record.revision}` || !Number.isSafeInteger(record.revision) || !record.revision ||
        body?.context !== (queue ? "review" : "practice") || body.attempt_id !== record.attemptId || body.revision !== record.revision ||
        (queue && (body.card_id !== queue.cardId || body.queue_entry_id !== queue.entryId || body.queue_cycle !== queue.cycle || record.revision !== queue.revision)) ||
        receipt?.attempt_id !== record.attemptId || receipt.pending_self_assessment !== true) continue;
    const boundary = await store.boundary(boundaryKey(saved.scope), scopeSelector(saved.scope));
    if (!isStudyIntentStoreCurrent(store)) throw new Error("Study authority changed while locating the original attempt.");
    if (record.sequence > boundary.value) candidates.push(record.attemptId);
  }
  if (candidates.length > 1) throw new Error("Multiple retained Study attempts need review before recovery.");
  return candidates[0];
}

let recovery: { store: IntentStore; signal: AbortSignal | undefined; promise: Promise<void> } | undefined;
/** Bounded, persistent round-robin recovery, including commands no longer in the issued queue. */
export async function recoverStudyRequests(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw signal.reason;
  const store = await studyIntentStore();
  if (signal?.aborted) throw signal.reason;
  // Pass cancellation belongs to its caller; the session still shares immutable command work.
  if (recovery?.store === store && recovery.signal === signal) return recovery.promise;
  const promise = (async () => {
    const coordinator = await studySession();
    const records = (await store.list(undefined, true)).filter(record => commandKinds.includes(record.kind as StudyCommandKind));
    for (const record of records.filter(record => isRetiredCommand(record.kind))) publishNotification({ severity: "warning", source: "study recovery",
      key: `study-command:${record.key}`, message: "This retired exercise remains held with its original request.",
      details: { attemptId: record.attemptId, requestId: record.requestId, endpoint: record.endpoint } });
    const pending = records.filter(record => !isRetiredCommand(record.kind));
    const cursorKey = "study-command-recovery:cursor";
    let cursor = await store.checkpoint(cursorKey);
    let cursorOwned = true;
    const sequence = Number(cursor ?? 0);
    const selected = [...pending.filter(record => record.sequence > sequence), ...pending.filter(record => record.sequence <= sequence)].slice(0, 20);
    for (const record of selected) {
      if (signal?.aborted) throw signal.reason;
      if (!isStudyIntentStoreCurrent(store)) return;
      const noticeKey = `study-command:${record.key}`;
      try {
        if (!await predecessorSettled(record, store)) continue;
        const result = await coordinator.checkSave(record.key);
        if (signal?.aborted) throw signal.reason;
        if (!isStudyIntentStoreCurrent(store)) return;
        if (result.state === "discarded") {
          publishNotification({ severity: "warning", source: "study recovery", key: noticeKey, message: (result.receipt as RejectionReceipt).message });
          continue;
        }
        if (result.state !== "committed") throw new PendingOperationError(record.requestId);
        const prior = notifications().find(notice => notice.key === noticeKey && !notice.resolvedAt);
        if (prior) resolveNotification(prior.id, { severity: "success", message: "Saved exercise result confirmed." });
      } catch (error) {
        if (signal?.aborted) throw signal.reason;
        if (!isStudyIntentStoreCurrent(store)) return;
        publishNotification({ severity: "warning", source: "study recovery", key: noticeKey,
          message: "An exercise result remains saved. Retry loading the queue to check its original receipt.",
          details: { attemptId: record.attemptId, requestId: record.requestId, endpoint: record.endpoint, error: String(error) } });
      } finally {
        if (cursorOwned && isStudyIntentStoreCurrent(store)) {
          cursorOwned = await store.compareCheckpoint(cursorKey, cursor, record.sequence);
          cursor = record.sequence;
        }
      }
    }
  })().finally(() => { if (recovery?.promise === promise) recovery = undefined; });
  recovery = { store, signal, promise }; return promise;
}
export async function startAnotherStudyPractice(scope: string, expectedBoundary?: number) {
  const store = await studyIntentStore();
  const expected = expectedBoundary ?? await studyPracticeBoundary(scope);
  return (await store.boundary(boundaryKey(scope), scopeSelector(scope), expected, records => !records.some(record =>
    (record.payload as FrozenStudyRequest).scope === practiceScope(scope) && record.state === "committed" &&
    (record.receipt as { pending_self_assessment?: boolean })?.pending_self_assessment === true && !records.some(assessment =>
      (assessment.payload as FrozenStudyRequest).scope === `${practiceScope(scope)}:self-assess` && assessment.attemptId === record.attemptId &&
      assessment.state === "committed" && (assessment.receipt as { pending_self_assessment?: boolean })?.pending_self_assessment !== true)))).value;
}
