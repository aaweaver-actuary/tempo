import { readPendingAnalysisPasteRecord, writePendingAnalysisPasteRecord, clearPendingAnalysisPasteRecord } from "../../database/browser/adapters/authoring/analysis-paste";
import { API_URL } from "../const";
import { analysisPasteCommitSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

type AnalysisPasteCommand = {
  text: string;
  starting_fen: string | null;
  source_gap_id?: string;
  preview_token: string;
  selections: Array<{
    index: number;
    repertoire_id: string;
    acknowledge_conflict: boolean;
  }>;
};
type PendingPaste = { operationId: string; body: string };

function readPending(): PendingPaste | null {
  const parsed = readPendingAnalysisPasteRecord();
  if (parsed === undefined) return null;
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending analysis save is invalid. Restore browser data before retrying.");
  return parsed as PendingPaste;
}

export async function saveAnalysisPasteCommand(payload: AnalysisPasteCommand) {
  const body = JSON.stringify(payload);
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      clearPendingAnalysisPasteRecord();
      throw new Error(receipt.error?.message ?? "The earlier analysis save failed.");
    }
    if (receipt.state === "complete") {
      clearPendingAnalysisPasteRecord();
      if (pending.body === body) return analysisPasteCommitSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    writePendingAnalysisPasteRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/repertoire/paste/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, analysisPasteCommitSchema, "saved pasted analysis");
  clearPendingAnalysisPasteRecord();
  return result;
}
