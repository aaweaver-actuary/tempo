import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";

export type PendingTeachingState = { cardId: string; revision: number; ply: number };

const storageKey = "tempo-pending-teaching-states-v1";
let activeFlush: Promise<void> | undefined;

export function pendingTeachingStates(): PendingTeachingState[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((entry) =>
    typeof entry !== "object" || entry === null ||
    typeof entry.cardId !== "string" || !entry.cardId ||
    !Number.isSafeInteger(entry.revision) || entry.revision < 1 ||
    !Number.isSafeInteger(entry.ply) || entry.ply < 0))
    throw new Error("Saved teaching states are invalid. Restore your browser data before continuing.");
  return parsed as PendingTeachingState[];
}

function stateKey(state: PendingTeachingState): string {
  return `${state.cardId}:${state.revision}:${state.ply}`;
}

export function enqueueTeachingState(state: PendingTeachingState): void {
  if (!state.cardId || !Number.isSafeInteger(state.revision) || state.revision < 1 ||
      !Number.isSafeInteger(state.ply) || state.ply < 0)
    throw new Error("Teaching state has no valid card, revision, or ply.");
  const pending = pendingTeachingStates();
  if (!pending.some((saved) => stateKey(saved) === stateKey(state)))
    localStorage.setItem(storageKey, JSON.stringify([...pending, state]));
}

async function saveTeachingStates(): Promise<void> {
  while (pendingTeachingStates().length) {
    const state = pendingTeachingStates()[0];
    let response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(state.cardId)}/teaching`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `teaching:${stateKey(state)}`,
      },
      body: JSON.stringify({ revision: state.revision, ply: state.ply }),
    });
    response = await confirmOperationResponse(response);
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(body.detail ?? `Teaching save returned HTTP ${response.status}.`);
    }
    localStorage.setItem(storageKey, JSON.stringify(
      pendingTeachingStates().filter((saved) => stateKey(saved) !== stateKey(state)),
    ));
  }
}

export function flushTeachingStates(): Promise<void> {
  activeFlush ??= saveTeachingStates().finally(() => { activeFlush = undefined; });
  return activeFlush;
}
