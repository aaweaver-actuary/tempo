import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";

const PENDING_KEY = "tempo-pending-repertoire-rename-v1";
type PendingRename = { operationId: string; repertoireId: string; name: string };

function readPendingRename(): PendingRename | null {
  const value = localStorage.getItem(PENDING_KEY);
  if (!value) return null;
  const parsed: unknown = JSON.parse(value);
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
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier repertoire rename failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.repertoireId === repertoireId && pending.name === name) return;
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.repertoireId !== repertoireId || pending.name !== name)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId, name };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ name }),
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not rename this repertoire");
  localStorage.removeItem(PENDING_KEY);
}
