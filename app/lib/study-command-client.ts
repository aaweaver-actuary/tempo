import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import { notifications, publishNotification, resolveNotification } from "./notifications";
import type { IntentRecord } from "@tempo/browser-intents";
import { confirmOperationResponse, PendingOperationError, readOperationResponse } from "./operation-status";
import { studyIntentStore } from "./study-intent-storage";

type FrozenStudyRequest = { body: Record<string, unknown>; headers: Record<string, string>; scope: string };
async function deliver(record: IntentRecord, signal: AbortSignal) {
  const request = record.payload as FrozenStudyRequest;
  const store = await studyIntentStore();
  const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
  await store.update(record.key, current => ({ ...current, data: { started: true } }));
  let response = started ? await readOperationResponse(record.requestId, { allowMissing: true, signal }) : new Response(null, { status: 404 });
  let body: Record<string, unknown> | undefined;
  if (response.ok) body = await response.clone().json() as Record<string, unknown>;
  // A completed transport response requesting analysis is not a completed exercise.
  if (response.status === 404 || body?.status === "needs_analysis") response = await fetch(record.endpoint, {
    method: "POST", signal, headers: request.headers, body: JSON.stringify(request.body),
  });
  response = await confirmOperationResponse(response, { signal });
  if (response.status === 202) throw new PendingOperationError(record.requestId);
  body = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Study save returned HTTP ${response.status}. Retry the original request.`);
  return assessResponse(record, body);
}

function assessResponse(record: IntentRecord, body: Record<string, unknown>) {
  if (record.kind === "study-command" && body.attempt_id !== record.attemptId) throw new Error("The service did not confirm the original study attempt.");
  if (record.kind === "defense-command" && !["correct", "incorrect", "needs_analysis", "ambiguous", "illegal", "ready_for_move"].includes(String(body.status)))
    throw new Error("The service did not return a recognized defense assessment.");
  return { state: body.status === "needs_analysis" ? "pending" as const : "committed" as const, receipt: body };
}
async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
  const response = await readOperationResponse(record.requestId, { allowMissing: true, signal });
  if (response.status === 404) return undefined;
  return assessResponse(record, await response.json() as Record<string, unknown>);
}

registerStudyDeliveryAdapter("study-command", deliver, checkReceipt);
registerStudyDeliveryAdapter("defense-command", deliver, checkReceipt);

export async function executeStudyRequest(input: { scope: string; endpoint: string; body: Record<string, unknown>; requestId: string; attemptId?: string; revision?: number; kind?: "study-command" | "defense-command" }): Promise<Response> {
  const store = await studyIntentStore();
  const key = `${input.scope}:${input.requestId}`;
  const previous = await store.get(key);
  const coordinator = await studySession();
  if (coordinator.getSnapshot().key !== key) coordinator.start(key);
  const result = await coordinator.submit({ key, kind: input.kind ?? "study-command", attemptId: input.attemptId ?? String(input.body.attempt_id ?? input.requestId),
    requestId: input.requestId, endpoint: input.endpoint, revision: input.revision ?? Number(input.body.revision ?? input.body.exercise_revision ?? 1),
    createdAt: previous?.createdAt ?? new Date().toISOString(), payload: { body: input.body, scope: input.scope,
      headers: { "Content-Type": "application/json", "Idempotency-Key": input.requestId } } });
  if (!result.receipt) throw new PendingOperationError(input.requestId);
  return Response.json(result.receipt);
}

/** Restore the last request in an exercise scope without deriving another payload from fresh UI state. */
export async function retainedStudyRequest(scope: string): Promise<{ requestId: string; attemptId: string; revision?: number; body: Record<string, unknown>; receipt?: unknown } | undefined> {
  const store = await studyIntentStore();
  const after = Number(await store.checkpoint(`practice-reset:${scope}`) ?? 0);
  const retained = (await store.list()).filter(record => ["study-command", "defense-command"].includes(record.kind) &&
    (record.payload as FrozenStudyRequest).scope === scope && record.sequence > after && record.state !== "discarded").at(-1);
  return retained ? { requestId: retained.requestId, attemptId: retained.attemptId, revision: retained.revision, body: (retained.payload as FrozenStudyRequest).body, receipt: retained.receipt } : undefined;
}

/** Recover original commands even when the authoritative queue has advanced past their component. */
export async function recoverStudyRequests(signal?: AbortSignal): Promise<void> {
  const store = await studyIntentStore();
  const coordinator = await studySession();
  const pending = (await store.list(undefined, true)).filter(record => ["study-command", "defense-command"].includes(record.kind));
  // A bounded foreground pass; the next queue retry resumes any remaining commands.
  for (const record of pending.slice(0, 20)) {
    if (signal?.aborted) throw signal.reason;
    const noticeKey = `study-command:${record.key}`;
    try {
      const result = await coordinator.checkSave(record.key);
      if (signal?.aborted) throw signal.reason;
      if (result.state !== "committed") throw new PendingOperationError(record.requestId);
      const prior = notifications().find(notice => notice.key === noticeKey && !notice.resolvedAt);
      if (prior) resolveNotification(prior.id, { severity: "success", message: "Saved exercise result confirmed." });
    } catch (error) {
      if (signal?.aborted) throw signal.reason;
      publishNotification({ severity: "warning", source: "study recovery", key: noticeKey,
        message: "An exercise result remains saved. Retry loading the queue to check its original receipt.",
        details: { attemptId: record.attemptId, requestId: record.requestId, endpoint: record.endpoint, error: String(error) } });
      // Other immutable commands may already have terminal receipts. Keep recovering them.
    }
  }
}

export async function startAnotherStudyPractice(scope: string) {
  const store = await studyIntentStore();
  const prior = (await store.list()).filter(record => [scope, `${scope}:self-assess`].includes((record.payload as FrozenStudyRequest)?.scope));
  if (prior.some(record => record.state === "pending" || record.state === "conflicted")) throw new Error("Confirm the saved answer before starting another practice attempt.");
  const after = Math.max(0, ...prior.map(record => record.sequence));
  await store.checkpoint(`practice-reset:${scope}`, after);
  await store.checkpoint(`practice-reset:${scope}:self-assess`, after);
}
