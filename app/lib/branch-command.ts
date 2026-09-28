import { API_URL } from "../const";
import { branchResultSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-repertoire-branch-v1";
type BranchPayload = {
  repertoire_id: string;
  starting_fen: string;
  moves: string[];
  trained_color: string;
  name: string;
  source_gap_id?: string;
};
type PendingBranch = { operationId: string; body: string };

function readPending(): PendingBranch | null {
  const value = localStorage.getItem(PENDING_KEY);
  if (!value) return null;
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending repertoire branch is invalid. Restore browser data before retrying.");
  return parsed as PendingBranch;
}

export async function saveBranchCommand(payload: BranchPayload) {
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
      throw new Error(receipt.error?.message ?? "The earlier branch save failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.body === body) return branchResultSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/repertoire/branches`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not save this repertoire branch.");
  const result = await readJsonResponse(response, branchResultSchema, "saved repertoire branch");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
