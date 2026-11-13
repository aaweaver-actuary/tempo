import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { readPendingRepertoireRenameRecord, writePendingRepertoireRenameRecord, clearPendingRepertoireRenameRecord } from "../../database/browser/adapters/authoring/repertoire-rename";
import { API_URL } from "../const";
import { z } from "zod";
import { confirmOriginalOperationResponse, PendingOperationError } from "./operation-status";

type PendingRename = { operationId: string; repertoireId: string; name: string };
const renameResponseSchema = z.strictObject({ id: z.string(), name: z.string() });

function validateRename(raw: unknown, pending: PendingRename): void {
  const result = renameResponseSchema.parse(raw);
  // Python str.strip() owns the persisted value; retain the submitted bytes.
  const name = pending.name.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, "");
  if (result.id !== pending.repertoireId || result.name !== name)
    throw new Error("The service did not confirm the original repertoire rename.");
}

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
    const receipt = decodeOperationResult(await status.json(), pending.operationId, false);
    if (receipt.kind === "failed") {
      clearPendingRepertoireRenameRecord();
      throw new Error(receipt.error.message ?? "The earlier repertoire rename failed.");
    }
    if (receipt.kind === "complete") {
      validateRename(receipt.response, pending);
      clearPendingRepertoireRenameRecord();
      if (pending.repertoireId === repertoireId && pending.name === name) return;
      pending = null;
    } else if (receipt.kind === "pending") {
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
  response = await confirmOriginalOperationResponse(response, pending.operationId);
  if (!response.ok) throw new Error("Could not rename this repertoire");
  validateRename(await response.json(), pending);
  clearPendingRepertoireRenameRecord();
}
