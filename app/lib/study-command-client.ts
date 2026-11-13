import { defaultStudyClient, type StudyClient } from "./study-connection";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import { notifications, publishNotification, resolveNotification } from "./notifications";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { isStudyIntentStoreCurrent, studyIntentStore } from "../../database/browser/study-intent-storage";
import { studySettingsSchema } from "./study-connection";
import { z } from "zod";
import { tacticCardIdentityFromRequest } from "./tactic-card-identity";
import { decodeFrozenAttempt } from "../domain/study-exercise-transport";

type StudyCommandKind = "study-command" | "defense-command" | "tactic-command" | "endgame-command" | "burial-command" | "settings-command";
const commandKinds: StudyCommandKind[] = ["study-command", "defense-command", "tactic-command", "endgame-command", "burial-command", "settings-command"];
const retiredCommandKinds = new Set<StudyCommandKind>(["defense-command", "endgame-command"]);
function isRetiredCommand(kind: string): boolean {
  return retiredCommandKinds.has(kind as StudyCommandKind);
}
function requireRetainedCommand(kind: string): void {
  if (isRetiredCommand(kind)) throw new Error("This retired exercise is held on this device; its original request is preserved.");
}

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
function frozenIdentity(record: IntentRecord, parent?: IntentRecord): { context: "review" | "practice"; child: boolean } {
  const request = record.payload as FrozenStudyRequest | undefined;
  const path = new URL(record.endpoint, "https://study.invalid").pathname;
  const match = /^\/api\/studies\/([^/]+)\/exercises\/([^/]+)\/attempts(?:\/([^/]+)\/self-assess)?$/.exec(path);
  const child = Boolean(match?.[3]);
  const original = child ? parent : record;
  const originalRequest = original?.payload as FrozenStudyRequest | undefined;
  const context = originalRequest?.body?.context;
  if (!match || !request?.scope || !original || !originalRequest || !["review", "practice"].includes(String(context)) ||
      original.kind !== "study-command" || original.attemptId !== record.attemptId ||
      originalRequest.body.attempt_id !== record.attemptId || originalRequest.body.revision !== record.revision ||
      original.revision !== record.revision ||
      (child && (decodeURIComponent(match[3]) !== record.attemptId || request.scope !== `${originalRequest.scope}:self-assess` ||
        original.endpoint !== record.endpoint.replace(/\/[^/]+\/self-assess$/, ""))))
    throw new Error(`Retained exercise ${record.requestId} is held: original route or identity is ambiguous.`);
  return { context: context as "review" | "practice", child };
}
async function frozenParent(record: IntentRecord, store: IntentStore): Promise<IntentRecord | undefined> {
  const request = record.payload as FrozenStudyRequest;
  if (!request?.scope?.endsWith(":self-assess")) return undefined;
  const parents = (await store.list()).filter(candidate => candidate.kind === "study-command" && candidate.attemptId === record.attemptId &&
    (candidate.payload as FrozenStudyRequest)?.scope === request.scope.slice(0, -12));
  if (parents.length !== 1) throw new Error(`Retained exercise ${record.requestId} is held: original parent is ambiguous.`);
  return parents[0];
}
async function predecessorSettled(record: IntentRecord, store: IntentStore): Promise<boolean> {
  const request = record.payload as FrozenStudyRequest;
  const parentScope = request.scope.endsWith(":self-assess") ? request.scope.slice(0, -12) : undefined;
  const recognition = request.body.recognition_attempt_id;
  if (!parentScope && !recognition) return true;
  const parent = (await store.list()).find(candidate => candidate.kind === record.kind && candidate.attemptId === (parentScope ? record.attemptId : recognition) &&
    (parentScope ? (candidate.payload as FrozenStudyRequest).scope === parentScope : (candidate.payload as FrozenStudyRequest).scope.endsWith(":recognition")));
  if (record.kind === "study-command" && parentScope) {
    if (!parent || parent.state !== "committed") return false;
    const identity = frozenIdentity(record, parent);
    const receipt = decodeFrozenAttempt(parent.receipt, { attemptId: record.attemptId, context: identity.context });
    return "pending_self_assessment" in receipt && receipt.pending_self_assessment;
  }
  return parent?.state === "committed";
}
function bindAuthority(client: StudyClient) {
  const generation = client.generation, origin = client.origin;
  return () => {
    if (client.generation !== generation || client.origin !== origin)
      throw new Error("Study authority changed. Recover the original operation with its original service.");
  };
}

export function createStudyCommandDelivery(client: StudyClient, getStore: () => Promise<IntentStore>) {
  async function deliver(record: IntentRecord, signal: AbortSignal) {
    const requireAuthority = bindAuthority(client);
    const request = record.payload as FrozenStudyRequest;
    const store = await getStore();
    requireAuthority();
    const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
    if (!started) {
      requireRetainedCommand(record.kind);
      if (record.kind === "study-command") frozenIdentity(record, await frozenParent(record, store));
      requireAuthority();
    }
    await store.update(record.key, current => ({ ...current, data: { ...current.data as object, started: true } }));
    requireAuthority();
    try {
      let response = started ? await readOperationResponse(record.requestId, { allowMissing: true, signal, client }) : new Response(null, { status: 404 });
      requireAuthority();
      let body: Record<string, unknown> | undefined;
      if (response.ok) body = await response.clone().json() as Record<string, unknown>;
      requireAuthority();
      // A completed transport response requesting analysis is not a completed exercise.
      if (body?.status === "needs_analysis") throw new Error(`Retained exercise ${record.requestId} is held: legacy analysis is unavailable.`);
      if (response.status === 404) {
        requireRetainedCommand(record.kind);
        if (record.kind === "study-command") frozenIdentity(record, await frozenParent(record, store));
        if (!await predecessorSettled(record, store)) throw new PendingOperationError(record.requestId, "Confirm the original answer before recovering its dependent submission.");
        requireAuthority();
        response = await client.learningFetch(record.endpoint, { method: "POST", signal, headers: request.headers, body: JSON.stringify(request.body) });
      }
      requireAuthority();
      response = await confirmOriginalOperationResponse(response, record.requestId, { signal, client });
      requireAuthority();
      if (response.status === 202) throw new PendingOperationError(record.requestId);
      body = await response.json() as Record<string, unknown>;
      requireAuthority();
      if (!response.ok) {
        const message = typeof body.detail === "string" ? body.detail : `Study save returned HTTP ${response.status}.`;
        if (record.kind === "study-command" && response.status === 422) return rejected(record, 422, message, body);
        throw new Error(message + " Retry the original request.");
      }
      const result = await assessResponse(record, body);
      requireAuthority();
      return result;
    } catch (error) { return rejectionFromError(record, error); }
  }
  async function assessResponse(record: IntentRecord, body: Record<string, unknown>) {
    if (record.kind === "study-command") {
      const store = await getStore();
      const parent = await frozenParent(record, store);
      const identity = frozenIdentity(record, parent);
      if (identity.child && !await predecessorSettled(record, store)) throw new Error("The original parent receipt is not confirmed.");
      const result = decodeFrozenAttempt(body, { attemptId: record.attemptId, context: identity.context });
      if (identity.child && "pending_self_assessment" in result) throw new Error("The original self-assessment has not completed.");
    } else if (isRetiredCommand(record.kind)) throw new Error(`Retained exercise ${record.requestId} is held: retired exercise.`);
    if (record.kind === "tactic-command") {
      const result = z.strictObject({ card_id: z.string().min(1), mode: z.enum(["normal", "light", "hard"]), next_due: z.string().min(1) }).parse(body);
      const request = record.payload as FrozenStudyRequest;
      if (result.card_id !== await tacticCardIdentityFromRequest(request.body, record.attemptId))
        throw new Error("The tactic receipt does not match the original saved card.");
    }
    if (record.kind === "settings-command") studySettingsSchema.parse(body);
    if (record.kind === "burial-command" && (body.buried !== true || body.queue_entry_id !== Number(/entries\/(\d+)\//.exec(record.endpoint)?.[1])))
      throw new Error("Learning did not confirm the original burial.");
    return { state: body.status === "needs_analysis" ? "pending" as const : "committed" as const, receipt: body };
  }
  async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
    const requireAuthority = bindAuthority(client);
    try {
      const response = await readOperationResponse(record.requestId, { allowMissing: true, signal, client });
      requireAuthority();
      if (response.status === 404) return undefined;
      const body = await response.json() as Record<string, unknown>;
      requireAuthority();
      const result = await assessResponse(record, body);
      requireAuthority();
      return result;
    } catch (error) { return rejectionFromError(record, error); }
  }
  return { deliver, checkReceipt };
}
const defaultDelivery = createStudyCommandDelivery(defaultStudyClient, studyIntentStore);
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
