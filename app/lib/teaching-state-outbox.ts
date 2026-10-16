import { LEARNING_URL as API_URL, learningFetch as fetch } from "./study-connection";
import type { IntentRecord } from "@tempo/browser-intents";
import { confirmOperationResponse, readOperationResponse, PendingOperationError } from "./operation-status";
import { rememberTeachingFact, studyIntentRecords, studyIntentStore, refreshStudyIntents } from "./study-intent-storage";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";

export type PendingTeachingState = { cardId: string; revision: number; ply: number };
let activeFlush: Promise<void> | undefined;
let activeStore: Awaited<ReturnType<typeof studyIntentStore>> | undefined;
export function pendingTeachingStates(): PendingTeachingState[] {
  return studyIntentRecords("teaching").filter(record => record.state === "pending").map(record => record.payload as PendingTeachingState);
}
export async function enqueueTeachingState(state: PendingTeachingState): Promise<void> {
  if (!state.cardId || !Number.isSafeInteger(state.revision) || state.revision < 1 || !Number.isSafeInteger(state.ply) || state.ply < 0)
    throw new Error("Teaching state has no valid card, revision, or ply.");
  const id = `${state.cardId}:${state.revision}:${state.ply}`;
  await rememberTeachingFact({ key: id, ...state, kind: "seen" }, { key: `teaching:${id}`, kind: "teaching", attemptId: id, requestId: `teaching:${id}`,
    revision: state.revision, endpoint: `/api/cards/${encodeURIComponent(state.cardId)}/teaching`, createdAt: "teaching-fact", payload: state });
}
async function checkReceipt(record: IntentRecord, signal: AbortSignal) {
  const response = await readOperationResponse(record.requestId, { allowMissing: true, signal });
  if (response.status === 404) return undefined;
  return confirmedTeaching(record, await response.json());
}
function confirmedTeaching(record: IntentRecord, body: unknown) {
  const fact = body as Partial<PendingTeachingState & { taughtAt: string }>;
  const expected = record.payload as PendingTeachingState;
  if (!fact || fact.cardId !== expected.cardId || fact.revision !== expected.revision || fact.ply !== expected.ply || typeof fact.taughtAt !== "string" || !fact.taughtAt)
    throw new Error("Learning did not confirm the original teaching fact. Keep the pending request.");
  return { state: "committed" as const, receipt: body };
}
async function deliver(record: IntentRecord, signal: AbortSignal) {
  const store = await studyIntentStore();
  const state = record.payload as PendingTeachingState;
  const started = Boolean((record.data as { started?: boolean } | undefined)?.started);
  await store.update(record.key, latest => ({ ...latest, data: { started: true } }));
  if (started) { const receipt = await checkReceipt(record, signal); if (receipt) return receipt; }
  const delivered = await fetch(`${API_URL}${record.endpoint}`, {
    method: "POST", signal, headers: { "Content-Type": "application/json", "Idempotency-Key": record.requestId },
    body: JSON.stringify({ revision: state.revision, ply: state.ply }),
  });
  if (delivered.status === 202) {
    const pending = await delivered.clone().json() as { operation_id?: string };
    if (pending.operation_id && pending.operation_id !== record.requestId) throw new Error("Learning returned another teaching operation.");
  }
  const response = await confirmOperationResponse(delivered, { signal });
  if (response.status === 202) throw new PendingOperationError(record.requestId);
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? `Teaching save returned HTTP ${response.status}.`);
  }
  return confirmedTeaching(record, await response.json());
}
registerStudyDeliveryAdapter("teaching", deliver, checkReceipt);
async function saveTeachingStates(): Promise<void> {
  const coordinator = await studySession();
  for (const state of pendingTeachingStates()) {
    await coordinator.checkSave(`teaching:${state.cardId}:${state.revision}:${state.ply}`);
    await refreshStudyIntents();
  }
}
export async function flushTeachingStates(): Promise<void> {
  const store = await studyIntentStore();
  if (activeStore !== store) { activeStore = store; activeFlush = undefined; }
  if (!activeFlush) {
    const delivery = saveTeachingStates().finally(() => { if (activeFlush === delivery) activeFlush = undefined; });
    activeFlush = delivery;
  }
  return activeFlush;
}
