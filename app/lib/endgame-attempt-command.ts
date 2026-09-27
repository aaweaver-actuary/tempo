import { API_URL } from "../const";
import { endgameAttemptSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-endgame-attempt-v1";
type PendingAttempt = { operationId: string; templateId: string };

function readPending(): PendingAttempt | null {
  const value = localStorage.getItem(PENDING_KEY);
  if (!value) return null;
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("templateId" in parsed) || typeof parsed.templateId !== "string")
    throw new Error("The pending endgame attempt is invalid. Restore browser data before retrying.");
  return parsed as PendingAttempt;
}

export async function startEndgameAttempt(templateId: string) {
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier endgame attempt failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.templateId === templateId) return endgameAttemptSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.templateId !== templateId) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), templateId };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(
    `${API_URL}/api/endgames/templates/${encodeURIComponent(templateId)}/attempt`,
    { method: "POST", headers: { "Idempotency-Key": pending.operationId } },
  );
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not generate the endgame position.");
  const result = await readJsonResponse(response, endgameAttemptSchema, "endgame attempt");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
