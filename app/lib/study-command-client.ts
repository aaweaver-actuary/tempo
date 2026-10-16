import { learningFetch as fetch } from "./study-connection";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import { notifications, publishNotification, resolveNotification } from "./notifications";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { isStudyIntentStoreCurrent, studyIntentStore } from "./study-intent-storage";
import { endgameAttemptSchema } from "../domain/schemas";
import { studySettingsSchema } from "./study-connection";
import { z } from "zod";

type StudyCommandKind = "study-command" | "defense-command" | "tactic-command" | "endgame-command" | "burial-command" | "settings-command";
const commandKinds: StudyCommandKind[] = ["study-command", "defense-command", "tactic-command", "endgame-command", "burial-command", "settings-command"];

type FrozenStudyRequest = { body: Record<string, unknown>; headers: Record<string, string>; scope: string };
type RejectionReceipt = { type: "rejected"; requestId: string; attemptId: string; status: number; message: string; details?: unknown };
export class StudyRequestRejectedError extends Error {
  constructor(readonly receipt: RejectionReceipt) { super(receipt.message); this.name = "StudyRequestRejectedError"; }
}
function rejected(record: IntentRecord, status: number, message: string, details?: unknown) {
  return { state: "discarded" as const, receipt: { type: "rejected", requestId: record.requestId, attemptId: record.attemptId, status, message, details } satisfies RejectionReceipt };
}
function rejectionFromError(record: IntentRecord, error: unknown) {
  if (record.kind === "study-command" && error instanceof FailedOperationError && error.operationId === record.requestId && error.status === 422 && error.retryable !== true)
    return rejected(record, 422, error.message, { code: error.code, retryable: error.retryable, detail: error.detail });
  throw error;
}
async function predecessorSettled(record: IntentRecord, store: IntentStore): Promise<boolean> {
  const request = record.payload as FrozenStudyRequest;
  const parentScope = request.scope.endsWith(":self-assess") ? request.scope.slice(0, -12) : undefined;
  const recognition = request.body.recognition_attempt_id;
  if (!parentScope && !recognition) return true;
  const parent = (await store.list()).find(candidate => candidate.kind === record.kind && candidate.attemptId === (parentScope ? record.attemptId : recognition) &&
    (parentScope ? (candidate.payload as FrozenStudyRequest).scope === parentScope : (candidate.payload as FrozenStudyRequest).scope.endsWith(":recognition")));
  return parent?.state === "committed";
}
async function deliver(record: IntentRecord, signal: AbortSignal) {
  const request = record.payload as FrozenStudyRequest;
  const store = await studyIntentStore();
  const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
  await store.update(record.key, current => ({ ...current, data: { ...current.data as object, started: true } }));
  try {
    let response = started ? await readOperationResponse(record.requestId, { allowMissing: true, signal }) : new Response(null, { status: 404 });
    let body: Record<string, unknown> | undefined;
    if (response.ok) body = await response.clone().json() as Record<string, unknown>;
    // A completed transport response requesting analysis is not a completed exercise.
    if (response.status === 404 || body?.status === "needs_analysis") {
      if (!await predecessorSettled(record, store)) throw new PendingOperationError(record.requestId, "Confirm the original answer before recovering its dependent submission.");
      response = await fetch(record.endpoint, { method: "POST", signal, headers: request.headers, body: JSON.stringify(request.body) });
    }
    if (response.status === 202) {
      const operation = await response.clone().json() as { operation_id?: string };
      if (operation.operation_id && operation.operation_id !== record.requestId) throw new Error("The service did not confirm the original study operation.");
    }
    response = await confirmOperationResponse(response, { signal });
    if (response.status === 202) throw new PendingOperationError(record.requestId);
    body = await response.json() as Record<string, unknown>;
    if (!response.ok) {
      const message = typeof body.detail === "string" ? body.detail : `Study save returned HTTP ${response.status}.`;
      if (record.kind === "study-command" && response.status === 422) return rejected(record, 422, message, body);
      throw new Error(message + " Retry the original request.");
    }
    return assessResponse(record, body);
  } catch (error) { return rejectionFromError(record, error); }
}
function assessResponse(record: IntentRecord, body: Record<string, unknown>) {
  if (record.kind === "study-command" && body.attempt_id !== record.attemptId) throw new Error("The service did not confirm the original study attempt.");
  if (record.kind === "defense-command" && !["correct", "incorrect", "needs_analysis", "ambiguous", "illegal", "ready_for_move"].includes(String(body.status)))
    throw new Error("The service did not return a recognized defense assessment.");
  if (record.kind === "endgame-command") endgameAttemptSchema.parse(body);
  if (record.kind === "tactic-command") z.strictObject({ card_id: z.string().min(1), mode: z.enum(["normal", "light", "hard"]), next_due: z.string().min(1) }).parse(body);
  if (record.kind === "settings-command") studySettingsSchema.parse(body);
  if (record.kind === "burial-command" && (body.buried !== true || body.queue_entry_id !== Number(/entries\/(\d+)\//.exec(record.endpoint)?.[1])))
    throw new Error("Learning did not confirm the original burial.");
  return { state: body.status === "needs_analysis" ? "pending" as const : "committed" as const, receipt: body };
}
async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
  try {
    const response = await readOperationResponse(record.requestId, { allowMissing: true, signal });
    if (response.status === 404) return undefined;
    return assessResponse(record, await response.json() as Record<string, unknown>);
  } catch (error) { return rejectionFromError(record, error); }
}
for (const kind of commandKinds) registerStudyDeliveryAdapter(kind, deliver, checkReceipt);

function practiceScope(scope: string) { return scope.endsWith(":self-assess") ? scope.slice(0, -12) : scope; }
function boundaryKey(scope: string) { return `practice-reset:${practiceScope(scope)}`; }
function scopeSelector(scope: string) { const parent = practiceScope(scope); return (record: IntentRecord) => [parent, `${parent}:self-assess`].includes((record.payload as FrozenStudyRequest)?.scope); }
export async function studyPracticeBoundary(scope: string) {
  return (await (await studyIntentStore()).boundary(boundaryKey(scope), scopeSelector(scope))).value;
}
export async function executeStudyRequest(input: { scope: string; endpoint: string; body: Record<string, unknown>; requestId: string; attemptId?: string; revision?: number; kind?: StudyCommandKind; practiceBoundary?: number }): Promise<Response> {
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
    const pending = (await store.list(undefined, true)).filter(record => commandKinds.includes(record.kind as StudyCommandKind));
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
