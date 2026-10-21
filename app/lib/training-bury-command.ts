import { LEARNING_URL as API_URL, learningFetch as fetch, independentStudy } from "./study-connection";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse, retryBlockedOperation } from "./operation-status";
import { executeStudyRequest, retainedStudyRequest } from "./study-command-client";
import { studyIntentRecords } from "../../database/browser/study-intent-storage";

export async function buryTrainingEntry(queueEntryId: number, retry = false, identity?: { card_id: string; expected_revision?: number }): Promise<void> {
  if (independentStudy) {
    const scope = `burial:${queueEntryId}`;
    const retained = await retainedStudyRequest(scope);
    const requestId = retained?.requestId ?? crypto.randomUUID();
    const response = await executeStudyRequest({ scope, kind: "burial-command", requestId,
      endpoint: `${API_URL}/api/queue/entries/${queueEntryId}/bury`, body: retained?.body ?? identity ?? {} });
    await validateTrainingBurial(response, queueEntryId); return;
  }
  const operationKey = `tempo-bury-operation-${queueEntryId}`;
  const existingOperationId = localStorage.getItem(operationKey);
  const operationId = existingOperationId ?? crypto.randomUUID();
  const identityKey = `tempo-bury-identity-${queueEntryId}`;
  const savedIdentity = localStorage.getItem(identityKey);
  const requestBody = savedIdentity ?? (!existingOperationId && identity ? JSON.stringify(identity) : undefined);
  if (requestBody) localStorage.setItem(identityKey, requestBody);
  localStorage.setItem(operationKey, operationId);
  localStorage.setItem("tempo-pending-burial-entry", String(queueEntryId));
  try {
    if (retry) {
      let confirmed: Response | undefined;
      try { confirmed = await readOperationResponse(operationId); }
      catch (error) {
        if (!(error instanceof PendingOperationError)) throw error;
        if (error.blocked) {
          confirmed = await retryBlockedOperation(operationId);
        }
        // Other unresolved states can safely replay the original command.
      }
      if (confirmed) { await validateTrainingBurial(confirmed, queueEntryId); return; }
    }
    let response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/bury`, {
      method: "POST", headers: { "Idempotency-Key": operationId, ...(requestBody ? { "Content-Type": "application/json" } : {}) },
      body: requestBody,
    });
    // A direct server error can replay a permanently failed receipt, or hide
    // a committed command. Only its durable receipt resolves that ambiguity.
    if (response.status >= 500) {
      response = await readOperationResponse(operationId);
    } else response = await confirmOperationResponse(response);
    if (!response.ok) {
      // Request timeouts and rate limits leave the command retriable. Other
      // 4xx responses definitively reject this logical operation.
      if (response.status >= 400 && response.status < 500 &&
          response.status !== 408 && response.status !== 429)
        finishTrainingBurial(queueEntryId);
      const detail = (await response.json().catch(() => ({}))) as { detail?: string };
      throw new Error(detail.detail ?? `Local service returned HTTP ${response.status}.`);
    }
    await validateTrainingBurial(response, queueEntryId);
    // Keep this identity until queue refresh succeeds. Transport failures,
    // pending/blocked receipts and ambiguous confirmations must also retain it.
  } catch (error) {
    if (error instanceof FailedOperationError) finishTrainingBurial(queueEntryId);
    throw error;
  }
}

export function finishTrainingBurial(queueEntryId: number): void {
  if (independentStudy) return;
  localStorage.removeItem(`tempo-bury-operation-${queueEntryId}`);
  localStorage.removeItem(`tempo-bury-identity-${queueEntryId}`);
  if (localStorage.getItem("tempo-pending-burial-entry") === String(queueEntryId))
    localStorage.removeItem("tempo-pending-burial-entry");
}


export function hasPendingTrainingBurial(queueEntryId: number): boolean {
  if (independentStudy) return studyIntentRecords("burial-command").some(record => record.key.startsWith(`burial:${queueEntryId}:`) && record.state !== "discarded" && record.state !== "committed");
  return localStorage.getItem(`tempo-bury-operation-${queueEntryId}`) !== null;
}


async function validateTrainingBurial(response: Response, queueEntryId: number): Promise<void> {
  const result = await response.json() as { buried?: boolean; queue_entry_id?: number };
  if (result.buried !== true || result.queue_entry_id !== queueEntryId)
    throw new Error("The service did not confirm burial. Retry to check its result.");
}

export async function recoverTrainingBurial(queueEntryId: number): Promise<void> {
  if (independentStudy) { await buryTrainingEntry(queueEntryId, true); return; }
  const operationId = localStorage.getItem(`tempo-bury-operation-${queueEntryId}`);
  if (!operationId) throw new Error("The burial operation identity is unavailable.");
  try { await validateTrainingBurial(await readOperationResponse(operationId), queueEntryId); }
  catch (error) {
    if (error instanceof FailedOperationError) finishTrainingBurial(queueEntryId);
    throw error;
  }
}

// A single marker also recovers a committed entry absent from the active queue.
// Legacy operation keys are consulted only for known queue entries, never scanned.
export function pendingTrainingBurialEntry(knownEntries: number[] = []): number | undefined {
  if (independentStudy) {
    const record = studyIntentRecords("burial-command").find(record => record.state !== "committed" && record.state !== "discarded");
    return record ? Number(/entries\/(\d+)\//.exec(record.endpoint)?.[1]) : undefined;
  }
  const marker = localStorage.getItem("tempo-pending-burial-entry");
  const entryId = marker === null ? undefined : Number(marker);
  if (entryId !== undefined && Number.isSafeInteger(entryId) && entryId > 0 && hasPendingTrainingBurial(entryId))
    return entryId;
  if (marker !== null) localStorage.removeItem("tempo-pending-burial-entry");
  const knownEntryId = knownEntries.find(hasPendingTrainingBurial);
  if (knownEntryId !== undefined)
    localStorage.setItem("tempo-pending-burial-entry", String(knownEntryId));
  return knownEntryId;
}
