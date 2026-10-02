import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse, retryBlockedOperation } from "./operation-status";

export async function buryTrainingEntry(queueEntryId: number, retry = false): Promise<void> {
  const operationKey = `tempo-bury-operation-${queueEntryId}`;
  const operationId = localStorage.getItem(operationKey) ?? crypto.randomUUID();
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
      method: "POST", headers: { "Idempotency-Key": operationId },
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
  localStorage.removeItem(`tempo-bury-operation-${queueEntryId}`);
  if (localStorage.getItem("tempo-pending-burial-entry") === String(queueEntryId))
    localStorage.removeItem("tempo-pending-burial-entry");
}


export function hasPendingTrainingBurial(queueEntryId: number): boolean {
  return localStorage.getItem(`tempo-bury-operation-${queueEntryId}`) !== null;
}


async function validateTrainingBurial(response: Response, queueEntryId: number): Promise<void> {
  const result = await response.json() as { buried?: boolean; queue_entry_id?: number };
  if (result.buried !== true || result.queue_entry_id !== queueEntryId)
    throw new Error("The service did not confirm burial. Retry to check its result.");
}

export async function recoverTrainingBurial(queueEntryId: number): Promise<void> {
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
