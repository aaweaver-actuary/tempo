import { readPendingRepertoireRenameRecord, writePendingRepertoireRenameRecord, clearPendingRepertoireRenameRecord } from "../../database/browser/adapters/authoring/repertoire-rename";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";

type PendingRename = { operationId: string; repertoireId: string; name: string };

function readPendingRename(): PendingRename | null {
  const parsed = readPendingRepertoireRenameRecord();
  if (parsed === undefined) return null;
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string" ||
      !("name" in parsed) || typeof parsed.name !== "string")
    throw new Error("The pending repertoire rename is invalid. Restore browser data before retrying.");
  return parsed as PendingRename;
}

export async function renameRepertoireCommand(repertoireId: string, name: string): Promise<void> {
  let pending = readPendingRename();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as { state?: string; error?: { message?: string } };
    if (receipt.state === "failed") {
      clearPendingRepertoireRenameRecord();
      throw new Error(receipt.error?.message ?? "The earlier repertoire rename failed.");
    }
    if (receipt.state === "complete") {
      clearPendingRepertoireRenameRecord();
      if (pending.repertoireId === repertoireId && pending.name === name) return;
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.repertoireId !== repertoireId || pending.name !== name)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId, name };
    writePendingRepertoireRenameRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ name }),
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not rename this repertoire");
  clearPendingRepertoireRenameRecord();
}
