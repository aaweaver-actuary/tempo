import { API_URL } from "../const";
import { removeBranchResultSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-branch-removal-v1";
type BranchRemoval = { repertoire_id: string; starting_fen: string; moves: string[] };
type PendingRemoval = { operationId: string; body: string };

function readPending(): PendingRemoval | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending branch removal is invalid. Restore browser data before retrying.");
  return parsed as PendingRemoval;
}

export async function removeBranchCommand(payload: BranchRemoval) {
  const body = JSON.stringify(payload);
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier branch removal failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.body === body) return removeBranchResultSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/repertoire/branches/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, removeBranchResultSchema, "remove repertoire branch");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
