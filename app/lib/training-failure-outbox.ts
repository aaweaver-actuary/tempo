import { API_URL } from "../const";

const storageKey = "tempo-pending-training-failures-v1";
const requestTimeoutMs = 15_000;
let activeFlush: Promise<void> | undefined;

export function pendingTrainingFailures(): number[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((entryId) =>
    !Number.isSafeInteger(entryId) || entryId <= 0))
    throw new Error("Saved training failures are invalid. Restore your browser data before continuing.");
  return parsed as number[];
}

export function enqueueTrainingFailure(queueEntryId: number): void {
  if (!Number.isSafeInteger(queueEntryId) || queueEntryId <= 0)
    throw new Error("The active queue entry has no valid identity.");
  const pending = pendingTrainingFailures();
  if (!pending.includes(queueEntryId))
    localStorage.setItem(storageKey, JSON.stringify([...pending, queueEntryId]));
}

function removeTrainingFailure(queueEntryId: number): void {
  localStorage.setItem(storageKey, JSON.stringify(
    pendingTrainingFailures().filter((entryId) => entryId !== queueEntryId),
  ));
}

async function saveTrainingFailures(): Promise<void> {
  while (pendingTrainingFailures().length) {
    const queueEntryId = pendingTrainingFailures()[0];
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
    let response: Response;
    try {
      response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/fail`, {
        method: "POST", signal: controller.signal,
      });
    } catch (cause) {
      if (controller.signal.aborted)
        throw new Error("Saving the guided attempt timed out. Tempo will retry.", { cause });
      throw cause;
    } finally {
      window.clearTimeout(timeout);
    }
    if (response.status === 409) {
      removeTrainingFailure(queueEntryId);
      const body = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(body.detail ?? "This queue attempt is no longer active. Refresh the training queue.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(body.detail ?? `Local service returned HTTP ${response.status}.`);
    }
    removeTrainingFailure(queueEntryId);
  }
}

export function flushTrainingFailures(): Promise<void> {
  activeFlush ??= saveTrainingFailures().finally(() => { activeFlush = undefined; });
  return activeFlush;
}
