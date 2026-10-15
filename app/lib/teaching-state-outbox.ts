import { API_URL } from "../const";
import { confirmOperationResponse, readOperationResponse, PendingOperationError } from "./operation-status";
import { rememberTeachingFact, studyIntentRecords, studyIntentStore, updateStudyIntent } from "./study-intent-storage";

export type PendingTeachingState = { cardId: string; revision: number; ply: number };
let activeFlush: Promise<void> | undefined;
let activeStore: Awaited<ReturnType<typeof studyIntentStore>> | undefined;
const deliveryOwner = crypto.randomUUID();
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
async function saveTeachingStates(): Promise<void> {
  const store = await studyIntentStore();
  for (const state of pendingTeachingStates()) {
    const id = `teaching:${state.cardId}:${state.revision}:${state.ply}`;
    const token = await store.claim(id, deliveryOwner, Date.now(), 60_000);
    if (!token) continue;
    try {
      const current = await store.get(id);
      const started = Boolean((current?.data as { started?: boolean } | undefined)?.started);
      await store.update(id, latest => ({ ...latest, data: { started: true } }));
      let response = started ? await readOperationResponse(id, { allowMissing: true }) : new Response(null, { status: 404 });
      if (response.status === 404) response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(state.cardId)}/teaching`, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": id },
        body: JSON.stringify({ revision: state.revision, ply: state.ply }),
      });
      response = await confirmOperationResponse(response);
      if (response.status === 202) throw new PendingOperationError(id);
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `Teaching save returned HTTP ${response.status}.`);
      }
      await updateStudyIntent(id, current => ({ ...current, state: "committed", receipt: { confirmed: true } }));
    } finally { await store.release(id, token); }
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
