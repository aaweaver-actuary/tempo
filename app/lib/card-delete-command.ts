import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError, FailedOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-card-delete-v1";
const pendingSchema = z.strictObject({ operationId: z.string(), cardId: z.string(), expectedRevision: z.number().int().positive() });
const resultSchema = z.strictObject({ deleted: z.literal(true), card_id: z.string() });
export const deletionPreviewSchema = z.strictObject({ card_id: z.string(), revision: z.number().int().positive(), repertoires: z.array(z.strictObject({ id: z.string(), name: z.string() })) });

export function pendingCardDeletion() {
  const stored = localStorage.getItem(PENDING_KEY);
  return stored ? pendingSchema.parse(JSON.parse(stored)) : null;
}

export async function deleteCardCommand(cardId: string, expectedRevision: number): Promise<void> {
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
        localStorage.removeItem(PENDING_KEY);
        return;
      }
    } catch (failure) {
      if (failure instanceof FailedOperationError) localStorage.removeItem(PENDING_KEY);
      throw failure;
    }
  }
  if (!pending && (localStorage.getItem("tempo-pending-card-revision-v1") || localStorage.getItem("tempo-pending-prefix-split-v1")))
    throw new Error("Resolve the pending card edit before deleting a card.");
  pending ??= { operationId: crypto.randomUUID(), cardId, expectedRevision };
  localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  try {
    const response = await confirmOperationResponse(await fetch(`${API_URL}/api/cards/${encodeURIComponent(cardId)}?permanent=true&expected_revision=${expectedRevision}`, {
      method: "DELETE", headers: { "Idempotency-Key": pending.operationId },
    }));
    const result = await readJsonResponse(response, resultSchema, "delete card");
    if (result.card_id !== cardId) throw new Error("The deletion response names a different card.");
    localStorage.removeItem(PENDING_KEY);
  } catch (failure) {
    if (failure instanceof FailedOperationError) localStorage.removeItem(PENDING_KEY);
    throw failure;
  }
}
