import type { StudyClient } from "./study-client";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import { studySettingsSchema } from "./study-client";
import { z } from "zod";
import { tacticCardIdentityFromRequest } from "./tactic-card-identity";
import { decodeFrozenAttempt } from "../domain/study-exercise-transport";

export type StudyCommandKind = "study-command" | "defense-command" | "tactic-command" | "endgame-command" | "burial-command" | "settings-command";
export const commandKinds: StudyCommandKind[] = ["study-command", "defense-command", "tactic-command", "endgame-command", "burial-command", "settings-command"];
const retiredCommandKinds = new Set<StudyCommandKind>(["defense-command", "endgame-command"]);
export function isRetiredCommand(kind: string): boolean {
  return retiredCommandKinds.has(kind as StudyCommandKind);
}
export function requireRetainedCommand(kind: string): void {
  if (isRetiredCommand(kind)) throw new Error("This retired exercise is held on this device; its original request is preserved.");
}

export type FrozenStudyRequest = { body: Record<string, unknown>; headers: Record<string, string>; scope: string };
export type RejectionReceipt = { type: "rejected"; requestId: string; attemptId: string; status: number; message: string; details?: unknown };
export class StudyRequestRejectedError extends Error {
  constructor(readonly receipt: RejectionReceipt) { super(receipt.message); this.name = "StudyRequestRejectedError"; }
}
function rejected(record: IntentRecord, status: number, message: string, details?: unknown) {
  return { state: "discarded" as const, receipt: { type: "rejected", requestId: record.requestId, attemptId: record.attemptId, status, message, details } satisfies RejectionReceipt };
}
function rejectionFromError(record: IntentRecord, error: unknown, FailedOperationError: typeof import("./operation-status").FailedOperationError) {
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
export async function predecessorSettled(record: IntentRecord, store: IntentStore): Promise<boolean> {
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

export function createStudyCommandDelivery(client: StudyClient, getStore: () => Promise<IntentStore>, operations: Pick<typeof import("./operation-status"), "confirmOriginalOperationResponse" | "readOperationResponse" | "FailedOperationError" | "PendingOperationError">) {
  const { confirmOriginalOperationResponse, readOperationResponse, PendingOperationError } = operations;
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
    } catch (error) { return rejectionFromError(record, error, operations.FailedOperationError); }
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
    } catch (error) { return rejectionFromError(record, error, operations.FailedOperationError); }
  }
  return { deliver, checkReceipt };
}
