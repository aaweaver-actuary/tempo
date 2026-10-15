import { createStudySession } from "@tempo/study-session";
import type { IntentRecord } from "@tempo/browser-intents";
import { confirmOperationResponse, PendingOperationError, readOperationResponse } from "./operation-status";
import { studyIntentStore, putStudyIntent } from "./study-intent-storage";

type FrozenStudyRequest = { body: Record<string, unknown>; headers: Record<string, string>; scope: string };
const sessions = new WeakMap<object, ReturnType<typeof createStudySession>>();
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
  if (record.kind === "study-command" && typeof body.attempt_id !== "string") throw new Error("The service did not confirm the original study attempt.");
  if (record.kind === "defense-command" && !["correct", "incorrect", "needs_analysis", "ambiguous", "illegal", "ready_for_move"].includes(String(body.status)))
    throw new Error("The service did not return a recognized defense assessment.");
  return { state: body.status === "needs_analysis" ? "pending" as const : "committed" as const, receipt: body };
}

export async function executeStudyRequest(input: { scope: string; endpoint: string; body: Record<string, unknown>; requestId: string; kind?: "study-command" | "defense-command" }): Promise<Response> {
  const store = await studyIntentStore();
  const key = `${input.scope}:${input.requestId}`;
  const previous = await store.get(key);
  await putStudyIntent({ key, kind: input.kind ?? "study-command", attemptId: String(input.body.attempt_id ?? input.requestId),
    requestId: input.requestId, endpoint: input.endpoint, revision: Number(input.body.revision ?? input.body.exercise_revision ?? 1),
    createdAt: previous?.createdAt ?? new Date().toISOString(), payload: { body: input.body, scope: input.scope,
      headers: { "Content-Type": "application/json", "Idempotency-Key": input.requestId } } });
  let coordinator = sessions.get(store);
  if (!coordinator) { coordinator = createStudySession({ store, learning: { deliver } }); sessions.set(store, coordinator); }
  const result = await coordinator.checkSave(key);
  if (!result.receipt) throw new PendingOperationError(input.requestId);
  return Response.json(result.receipt);
}

/** Restore the last request in an exercise scope without deriving another payload from fresh UI state. */
export async function retainedStudyRequest(scope: string): Promise<{ requestId: string; body: Record<string, unknown>; receipt?: unknown } | undefined> {
  const store = await studyIntentStore();
  const retained = (await store.list()).filter(record => ["study-command", "defense-command"].includes(record.kind) &&
    (record.payload as FrozenStudyRequest).scope === scope && record.state !== "discarded").at(-1);
  return retained ? { requestId: retained.requestId, body: (retained.payload as FrozenStudyRequest).body, receipt: retained.receipt } : undefined;
}
