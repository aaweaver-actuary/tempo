import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { readPendingBranchRecord, writePendingBranchRecord, clearPendingBranchRecord } from "../../database/browser/adapters/authoring/branch";
import { API_URL } from "../const";
import { branchResultSchema } from "../domain/schemas";
import { confirmOriginalOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

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
  const parsed = readPendingBranchRecord();
  if (parsed === undefined) return null;
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
    const receipt = decodeOperationResult(await status.json(), pending.operationId, false);
    if (receipt.kind === "failed") {
      clearPendingBranchRecord();
      throw new Error(receipt.error.message ?? "The earlier branch save failed.");
    }
    if (receipt.kind === "complete") {
      const result = branchResultSchema.parse(receipt.response);
      clearPendingBranchRecord();
      if (pending.body === body) return result;
      pending = null;
    } else if (receipt.kind === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    writePendingBranchRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/repertoire/branches`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOriginalOperationResponse(response, pending.operationId);
  if (!response.ok) throw new Error("Could not save this repertoire branch.");
  const result = await readJsonResponse(response, branchResultSchema, "saved repertoire branch");
  clearPendingBranchRecord();
  return result;
}
