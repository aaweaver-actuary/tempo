import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-repertoire-delete-v1";
const deletedRepertoireSchema = z.strictObject({ deleted: z.literal(true), id: z.string() });
type PendingDelete = { operationId: string; repertoireId: string };

function readPending(): PendingDelete | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string")
    throw new Error("The pending repertoire deletion is invalid. Restore browser data before retrying.");
  return parsed as PendingDelete;
}

export async function deleteRepertoireCommand(repertoireId: string): Promise<void> {
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier repertoire deletion failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.repertoireId === repertoireId) {
        const result = deletedRepertoireSchema.parse(receipt.response);
        if (result.id !== repertoireId) throw new Error("The deletion receipt names a different repertoire.");
        return;
      }
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.repertoireId !== repertoireId)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}`, {
    method: "DELETE", headers: { "Idempotency-Key": pending.operationId },
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, deletedRepertoireSchema, "delete repertoire");
  if (result.id !== repertoireId) throw new Error("The deletion response names a different repertoire.");
  localStorage.removeItem(PENDING_KEY);
}
