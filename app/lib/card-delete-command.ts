import { readPendingCardDeletionRecord, readPendingCardEditBytes, writePendingCardDeletionRecord, clearPendingCardDeletionRecord } from "../../database/browser/adapters/authoring/card-delete";
import { independentStudy } from "./study-connection";
import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError, FailedOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const pendingSchema = z.strictObject({ operationId: z.string(), cardId: z.string(), expectedRevision: z.number().int().positive() });
const resultSchema = z.strictObject({ deleted: z.literal(true), card_id: z.string() });
export const deletionPreviewSchema = z.strictObject({ card_id: z.string(), revision: z.number().int().positive(), repertoires: z.array(z.strictObject({ id: z.string(), name: z.string() })) });

export function pendingCardDeletion() {
  const record = readPendingCardDeletionRecord();
  return record === undefined ? null : pendingSchema.parse(record);
}

export async function deleteCardCommand(cardId: string, expectedRevision: number): Promise<void> {
  if (independentStudy) throw new Error("Card deletion belongs to Workbench. Saved deletion work is retained.");
  let pending = pendingCardDeletion();
  if (pending) {
    if (pending.cardId !== cardId || pending.expectedRevision !== expectedRevision)
      throw new PendingOperationError(pending.operationId);
    try {
      const response = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
      if (response.status !== 404) {
        if (!response.ok) throw new PendingOperationError(pending.operationId);
        const receipt = await response.json() as { state?: string; response?: unknown; error?: { message?: string } };
        if (receipt.state === "failed") throw new FailedOperationError(receipt.error?.message ?? "The earlier card deletion failed.", pending.operationId);
        if (receipt.state !== "complete") throw new PendingOperationError(pending.operationId);
        const result = resultSchema.parse(receipt.response);
        if (result.card_id !== cardId) throw new Error("The deletion receipt names a different card.");
        clearPendingCardDeletionRecord();
        return;
      }
    } catch (failure) {
      if (failure instanceof FailedOperationError) clearPendingCardDeletionRecord();
      throw failure;
    }
  }
  if (!pending && readPendingCardEditBytes())
    throw new Error("Resolve the pending card edit before deleting a card.");
  pending ??= { operationId: crypto.randomUUID(), cardId, expectedRevision };
  writePendingCardDeletionRecord(pending);
  try {
    const response = await confirmOperationResponse(await fetch(`${API_URL}/api/cards/${encodeURIComponent(cardId)}?permanent=true&expected_revision=${expectedRevision}`, {
      method: "DELETE", headers: { "Idempotency-Key": pending.operationId },
    }));
    const result = await readJsonResponse(response, resultSchema, "delete card");
    if (result.card_id !== cardId) throw new Error("The deletion response names a different card.");
    clearPendingCardDeletionRecord();
  } catch (failure) {
    if (failure instanceof FailedOperationError) clearPendingCardDeletionRecord();
    throw failure;
  }
}
