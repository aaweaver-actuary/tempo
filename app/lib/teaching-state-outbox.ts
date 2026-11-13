import { type PendingTeachingState } from "./teaching-delivery";
export type { PendingTeachingState } from "./teaching-delivery";
import { rememberTeachingFact, studyIntentRecords, studyIntentStore, refreshStudyIntents } from "../../database/browser/study-intent-storage";
import { studySession } from "./study-session-runtime";

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
