import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";
import { pendingReviews } from "./review-outbox";

const storageKey = "tempo-pending-training-failures-v1";
const requestTimeoutMs = 15_000;
let activeFlush: Promise<void> | undefined;

type PendingTrainingFailure = { queueEntryId: number; operationId: string; backendId?: string; expectedRevision?: number };
type QueueEntryState = {
  state: "head" | "queued" | "completed" | "unavailable";
  attempt_failed: boolean;
};

export function isLegacyTrainingFailure(failure: PendingTrainingFailure): boolean {
  return failure.backendId === undefined || failure.expectedRevision === undefined;
}

function savedTrainingFailures(): PendingTrainingFailure[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    typeof item === "number"
      ? !Number.isSafeInteger(item) || item <= 0
      : !item || typeof item !== "object" ||
        !Number.isSafeInteger(item.queueEntryId) || item.queueEntryId <= 0 ||
        typeof item.operationId !== "string" || !item.operationId || item.operationId.length > 128 ||
        (item.backendId !== undefined && (typeof item.backendId !== "string" || !item.backendId)) ||
        (item.expectedRevision !== undefined && (!Number.isSafeInteger(item.expectedRevision) || item.expectedRevision < 1))))
    throw new Error("Saved training failures are invalid. Restore your browser data before continuing.");
  if (parsed.some((item) => typeof item === "number")) {
    const upgraded = parsed.map((item) => typeof item === "number"
      ? { queueEntryId: item, operationId: crypto.randomUUID() }
      : item) as PendingTrainingFailure[];
    localStorage.setItem(storageKey, JSON.stringify(upgraded));
    return upgraded;
  }
  return parsed as PendingTrainingFailure[];
}

export function pendingTrainingFailures(): number[] {
  return savedTrainingFailures().map((pending) => pending.queueEntryId);
}

export function pendingTrainingFailureContexts(): PendingTrainingFailure[] {
  return savedTrainingFailures();
}

export function enqueueTrainingFailure(queueEntryId: number, backendId?: string, expectedRevision?: number): void {
  if (!Number.isSafeInteger(queueEntryId) || queueEntryId <= 0)
    throw new Error("The active queue entry has no valid identity.");
  const pending = savedTrainingFailures();
  if (!pending.some((item) => item.queueEntryId === queueEntryId && item.backendId === backendId && item.expectedRevision === expectedRevision))
    localStorage.setItem(storageKey, JSON.stringify([...pending,
      { queueEntryId, operationId: crypto.randomUUID(), backendId, expectedRevision }]));
}

function removeTrainingFailure(queueEntryId: number, operationId?: string): void {
  localStorage.setItem(storageKey, JSON.stringify(
    savedTrainingFailures().filter((item) => item.queueEntryId !== queueEntryId || (operationId !== undefined && item.operationId !== operationId)),
  ));
}

export function clearTrainingFailureAfterReview(queueEntryId: number, backendId?: string, expectedRevision?: number): void {
  const matching = savedTrainingFailures().filter((item) => item.queueEntryId === queueEntryId &&
    (item.backendId === undefined || (item.backendId === backendId && item.expectedRevision === expectedRevision)));
  for (const item of matching) removeTrainingFailure(queueEntryId, item.operationId);
}

function rotateTrainingFailureOperation(operationId: string): void {
  localStorage.setItem(storageKey, JSON.stringify(savedTrainingFailures().map((item) =>
    item.operationId === operationId ? { ...item, operationId: crypto.randomUUID() } : item)));
}

async function rejectedFailureState(queueEntryId: number): Promise<QueueEntryState> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/state`,
      { signal: controller.signal });
  } finally { window.clearTimeout(timeout); }
  if (!response.ok) throw new Error(`Could not check guided-attempt state (HTTP ${response.status}).`);
  const body: unknown = await response.json();
  if (!body || typeof body !== "object" ||
      !("state" in body) || !["head", "queued", "completed", "unavailable"].includes(String(body.state)) ||
      !("attempt_failed" in body) || typeof body.attempt_failed !== "boolean")
    throw new Error("The local service returned an invalid guided-attempt state.");
  return body as QueueEntryState;
}

async function saveTrainingFailures(): Promise<void> {
  while (savedTrainingFailures().length) {
    const pendingFailure = savedTrainingFailures()[0];
    const { queueEntryId, operationId, backendId, expectedRevision } = pendingFailure;
    const earlierPendingReview = pendingReviews()[0];
    if (earlierPendingReview && earlierPendingReview.queueEntryId !== queueEntryId) return;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
    let response: Response;
    try {
      response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/fail`, {
        method: "POST", signal: controller.signal,
        headers: { "Idempotency-Key": operationId, ...(backendId ? { "Content-Type": "application/json" } : {}) },
        ...(backendId ? { body: JSON.stringify({ card_id: backendId, expected_revision: expectedRevision }) } : {}),
      });
      response = await confirmOperationResponse(response);
    } catch (cause) {
      if (controller.signal.aborted)
        throw new Error("Saving the guided attempt timed out. Tempo will retry.", { cause });
      throw cause;
    } finally {
      window.clearTimeout(timeout);
    }
    if (response.status === 409) {
      const conflict = await response.clone().json().catch(() => ({})) as { code?: string; detail?: string };
      if (conflict.code === "queue_attempt_unprovable") {
        if (!isLegacyTrainingFailure(pendingFailure)) removeTrainingFailure(queueEntryId, operationId);
        throw new Error(conflict.detail ?? "The guided attempt no longer matches this queue entry.");
      }
      const state = await rejectedFailureState(queueEntryId);
      if (state.state === "queued" || state.state === "head") {
        if (state.attempt_failed) removeTrainingFailure(queueEntryId, operationId);
        else rotateTrainingFailureOperation(operationId);
        return;
      }
      if (state.state === "completed") {
        removeTrainingFailure(queueEntryId, operationId);
        continue;
      }
      if (!isLegacyTrainingFailure(pendingFailure)) removeTrainingFailure(queueEntryId, operationId);
      throw new Error("This queue attempt is no longer available. Refresh the training queue.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(body.detail ?? `Local service returned HTTP ${response.status}.`);
    }
    removeTrainingFailure(queueEntryId, operationId);
  }
}

export function flushTrainingFailures(): Promise<void> {
  activeFlush ??= saveTrainingFailures().finally(() => { activeFlush = undefined; });
  return activeFlush;
}
