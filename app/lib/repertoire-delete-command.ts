import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError, FailedOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-repertoire-delete-v1";
const deletedRepertoireSchema = z.strictObject({ deleted: z.literal(true), id: z.string() });
export type LearnedCardsPolicy = "keep" | "delete";
type PendingDelete = { operationId: string; repertoireId: string; learnedCards: LearnedCardsPolicy };

function readPending(): PendingDelete | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string")
    throw new Error("The pending repertoire deletion is invalid. Restore browser data before retrying.");
  const legacy = parsed as Partial<PendingDelete>;
  if (legacy.learnedCards !== undefined && legacy.learnedCards !== "keep" && legacy.learnedCards !== "delete")
    throw new Error("The pending repertoire deletion has an invalid learned-card policy.");
  return { ...legacy, learnedCards: legacy.learnedCards ?? "delete" } as PendingDelete;
}

export const pendingRepertoireDeletion = readPending;

export async function deleteRepertoireCommand(repertoireId: string, learnedCards: LearnedCardsPolicy = "delete"): Promise<void> {
  let pending = readPending();
  if (pending) {
    if (pending.repertoireId !== repertoireId || pending.learnedCards !== learnedCards)
      throw new PendingOperationError(pending.operationId);
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (status.status !== 404) {
      if (!status.ok) throw new PendingOperationError(pending.operationId);
      const receipt = await status.json() as {
        state?: string; response?: unknown; error?: { message?: string };
      };
      if (receipt.state === "failed") {
        localStorage.removeItem(PENDING_KEY);
        throw new FailedOperationError(receipt.error?.message ?? "The earlier repertoire deletion failed.", pending.operationId);
      }
      if (receipt.state === "complete") {
        const result = deletedRepertoireSchema.parse(receipt.response);
        if (result.id !== repertoireId) throw new Error("The deletion receipt names a different repertoire.");
        localStorage.removeItem(PENDING_KEY);
        return;
      } else throw new PendingOperationError(pending.operationId);
    }
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId, learnedCards };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  try {
    const response = await confirmOperationResponse(await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}?learned_cards=${learnedCards}`, {
      method: "DELETE", headers: { "Idempotency-Key": pending.operationId },
    }));
    const result = await readJsonResponse(response, deletedRepertoireSchema, "delete repertoire");
    if (result.id !== repertoireId) throw new Error("The deletion response names a different repertoire.");
    localStorage.removeItem(PENDING_KEY);
  } catch (failure) {
    if (failure instanceof FailedOperationError) localStorage.removeItem(PENDING_KEY);
    throw failure;
  }
}
