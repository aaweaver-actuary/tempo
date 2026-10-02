import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, readOperationResponse } from "./operation-status";

export async function buryTrainingEntry(queueEntryId: number): Promise<void> {
  const operationKey = `tempo-bury-operation-${queueEntryId}`;
  const operationId = localStorage.getItem(operationKey) ?? crypto.randomUUID();
  localStorage.setItem(operationKey, operationId);
  try {
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
    const result = await response.json() as { buried?: boolean; queue_entry_id?: number };
    if (result.buried !== true || result.queue_entry_id !== queueEntryId)
      throw new Error("The service did not confirm burial. Retry to check its result.");
    // Keep this identity until queue refresh succeeds. Transport failures,
    // pending/blocked receipts and ambiguous confirmations must also retain it.
  } catch (error) {
    if (error instanceof FailedOperationError) finishTrainingBurial(queueEntryId);
    throw error;
  }
}

export function finishTrainingBurial(queueEntryId: number): void {
  localStorage.removeItem(`tempo-bury-operation-${queueEntryId}`);
}


export function hasPendingTrainingBurial(queueEntryId: number): boolean {
  return localStorage.getItem(`tempo-bury-operation-${queueEntryId}`) !== null;
}
