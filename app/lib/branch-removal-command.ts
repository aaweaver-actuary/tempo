import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { readPendingBranchRemovalRecord, writePendingBranchRemovalRecord, clearPendingBranchRemovalRecord } from "../../database/browser/adapters/authoring/branch-removal";
import { API_URL } from "../const";
import { removeBranchResultSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

type BranchRemoval = { repertoire_id: string; starting_fen: string; moves: string[] };
type PendingRemoval = { operationId: string; body: string };

function readPending(): PendingRemoval | null {
  const parsed = readPendingBranchRemovalRecord();
  if (parsed === undefined) return null;
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
    const receipt = decodeOperationResult(await status.json(), pending.operationId, false);
    if (receipt.kind === "failed") {
      clearPendingBranchRemovalRecord();
      throw new Error(receipt.error.message ?? "The earlier branch removal failed.");
    }
    if (receipt.kind === "complete") {
      const result = removeBranchResultSchema.parse(receipt.response);
      clearPendingBranchRemovalRecord();
      if (pending.body === body) return result;
      pending = null;
    } else if (receipt.kind === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    writePendingBranchRemovalRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/repertoire/branches/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, removeBranchResultSchema, "remove repertoire branch");
  clearPendingBranchRemovalRecord();
  return result;
}
