import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";

export async function buryTrainingEntry(queueEntryId: number): Promise<void> {
  const operationKey = `tempo-bury-operation-${queueEntryId}`;
  const operationId = localStorage.getItem(operationKey) ?? crypto.randomUUID();
  localStorage.setItem(operationKey, operationId);
  let response = await fetch(`${API_URL}/api/queue/entries/${queueEntryId}/bury`, {
    method: "POST", headers: { "Idempotency-Key": operationId },
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(detail.detail ?? `Local service returned HTTP ${response.status}.`);
  }
  const result = await response.json() as { buried?: boolean; queue_entry_id?: number };
  if (result.buried !== true || result.queue_entry_id !== queueEntryId)
    throw new Error("The service did not confirm burial. Retry to check its result.");
  // Keep this identity until the queue refresh succeeds, so a failed refresh
  // can replay the confirmed command instead of burying another card.
}

export function finishTrainingBurial(queueEntryId: number): void {
  localStorage.removeItem(`tempo-bury-operation-${queueEntryId}`);
}
