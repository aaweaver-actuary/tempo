import { putStudyIntent, studyIntentRecords, studyIntentStore, updateStudyIntent } from "./study-intent-storage";
import { API_URL } from "../const";
import { confirmOperationResponse, readOperationResponse, PendingOperationError, FailedOperationError } from "./operation-status";
import { pendingReviews } from "./review-outbox";

const deliveryOwner = crypto.randomUUID();
const requestTimeoutMs = 15_000;
let activeFlush: Promise<void> | undefined;
let activeStore: Awaited<ReturnType<typeof studyIntentStore>> | undefined;

type PendingTrainingFailure = { queueEntryId: number; operationId: string; backendId?: string; expectedRevision?: number };
type QueueEntryState = {
  state: "head" | "queued" | "completed" | "unavailable";
  attempt_failed: boolean;
};

export function isLegacyTrainingFailure(failure: PendingTrainingFailure): boolean {
  return failure.backendId === undefined || failure.expectedRevision === undefined;
}

function savedTrainingFailures(): PendingTrainingFailure[] {
  return studyIntentRecords("failure").filter(record => record.state === "pending")
    .map(record => { const marker = (record.data ?? record.payload) as PendingTrainingFailure & { deliveryStarted?: boolean };
      const identity = { ...marker }; delete identity.deliveryStarted; return identity; });
}

export function pendingTrainingFailures(): number[] {
  return savedTrainingFailures().map((pending) => pending.queueEntryId);
}

export function pendingTrainingFailureContexts(): PendingTrainingFailure[] {
  return savedTrainingFailures();
}

export async function enqueueTrainingFailure(queueEntryId: number, backendId?: string, expectedRevision?: number): Promise<void> {
  const store = await studyIntentStore();
  if (!Number.isSafeInteger(queueEntryId) || queueEntryId <= 0)
    throw new Error("The active queue entry has no valid identity.");
  const retained = await store.list("failure");
  if (retained.some(record => { const item = record.payload as PendingTrainingFailure;
    return record.state !== "discarded" && item.queueEntryId === queueEntryId && item.backendId === backendId && item.expectedRevision === expectedRevision;
  })) return;
  const marker = { queueEntryId, operationId: crypto.randomUUID(), backendId, expectedRevision };
  await retainFailure(marker);
}
async function retainFailure(marker: PendingTrainingFailure) {
  await putStudyIntent({ key: `failure:${marker.operationId}`, kind: "failure", attemptId: `queue:${marker.queueEntryId}:${marker.expectedRevision ?? "legacy"}`,
    requestId: marker.operationId, endpoint: `/api/queue/entries/${marker.queueEntryId}/fail`, revision: marker.expectedRevision,
    createdAt: new Date().toISOString(), payload: marker }, marker);
}
async function removeTrainingFailure(queueEntryId: number, operationId?: string): Promise<void> {
  for (const marker of savedTrainingFailures().filter(item => item.queueEntryId === queueEntryId && (!operationId || item.operationId === operationId)))
    await updateStudyIntent(`failure:${marker.operationId}`, current => ({ ...current, state: "committed" }));
}
export async function clearTrainingFailureAfterReview(queueEntryId: number, backendId?: string, expectedRevision?: number): Promise<void> {
  for (const marker of savedTrainingFailures().filter(item => !isLegacyTrainingFailure(item) && item.queueEntryId === queueEntryId && item.backendId === backendId && item.expectedRevision === expectedRevision))
    await removeTrainingFailure(queueEntryId, marker.operationId);
}
async function rotateTrainingFailureOperation(operationId: string): Promise<void> {
  const marker = savedTrainingFailures().find(item => item.operationId === operationId);
  if (!marker) return;
  // The old request was authoritatively rejected. Retain it and persist a separate command before retry.
  await retainFailure({ ...marker, operationId: crypto.randomUUID() });
  await updateStudyIntent(`failure:${operationId}`, current => ({ ...current, state: "discarded", receipt: { replacedAfterAuthoritativeStateCheck: true } }));
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
  await studyIntentStore();
  while (savedTrainingFailures().length) {
    const pendingFailure = savedTrainingFailures()[0];
    const { queueEntryId, operationId, backendId, expectedRevision } = pendingFailure;
    const earlierPendingReview = pendingReviews()[0];
    if (earlierPendingReview && earlierPendingReview.queueEntryId !== queueEntryId) return;
    const store = await studyIntentStore();
    const token = await store.claim(`failure:${operationId}`, deliveryOwner, Date.now(), 60_000);
    if (!token) return;
    try {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);
    let response: Response;
    try {
      const original = await store.get(`failure:${operationId}`);
      const started = Boolean((original?.data as PendingTrainingFailure & { deliveryStarted?: boolean } | undefined)?.deliveryStarted);
      await store.update(`failure:${operationId}`, current => ({ ...current, data: { ...(current.data as PendingTrainingFailure), deliveryStarted: true } }));
      try { response = started ? await readOperationResponse(operationId, { allowMissing: true, signal: controller.signal }) : new Response(null, { status: 404 }); }
      catch (error) {
        if (!(error instanceof FailedOperationError) || error.status !== 409) throw error;
        response = Response.json({ code: error.code, detail: error.message }, { status: 409 });
      }
      if (response.status === 404) response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/fail`, {
        method: "POST", signal: controller.signal,
        headers: { "Idempotency-Key": operationId, ...(backendId ? { "Content-Type": "application/json" } : {}) },
        ...(backendId ? { body: JSON.stringify({ card_id: backendId, expected_revision: expectedRevision }) } : {}),
      });
      response = await confirmOperationResponse(response, { signal: controller.signal });
      if (response.status === 202) throw new PendingOperationError(operationId);
    } catch (cause) {
      if (controller.signal.aborted)
        throw new Error("Saving the guided attempt timed out. Tempo will retry.", { cause });
      throw cause;
    } finally {
      window.clearTimeout(timeout);
    }
    if (await studyIntentStore() !== store) return;
    if (response.status === 409) {
      const conflict = await response.clone().json().catch(() => ({})) as { code?: string; detail?: string };
      if (conflict.code === "queue_attempt_unprovable") {
        if (!isLegacyTrainingFailure(pendingFailure)) await removeTrainingFailure(queueEntryId, operationId);
        throw new Error(conflict.detail ?? "The guided attempt no longer matches this queue entry.");
      }
      const state = await rejectedFailureState(queueEntryId);
      if (state.state === "queued" || state.state === "head") {
        if (state.attempt_failed) await removeTrainingFailure(queueEntryId, operationId);
        else await rotateTrainingFailureOperation(operationId);
        return;
      }
      if (state.state === "completed") {
        await removeTrainingFailure(queueEntryId, operationId);
        continue;
      }
      if (!isLegacyTrainingFailure(pendingFailure)) await removeTrainingFailure(queueEntryId, operationId);
      throw new Error("This queue attempt is no longer available. Refresh the training queue.");
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { detail?: string };
      throw new Error(body.detail ?? `Local service returned HTTP ${response.status}.`);
    }
    await removeTrainingFailure(queueEntryId, operationId);
    } finally { await store.release(`failure:${operationId}`, token); }
  }
}

export async function flushTrainingFailures(): Promise<void> {
  const store = await studyIntentStore();
  if (activeStore !== store) { activeStore = store; activeFlush = undefined; }
  if (!activeFlush) {
    const delivery = saveTrainingFailures().finally(() => { if (activeFlush === delivery) activeFlush = undefined; });
    activeFlush = delivery;
  }
  return activeFlush;
}
