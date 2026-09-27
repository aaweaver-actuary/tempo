import { API_URL } from "../const";
import { endgameCreatedSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-endgame-template-v1";
type TemplateRequest = {
  name: string; white_material: string; black_material: string;
  trained_color: string; goal_mix: string;
};
type PendingTemplate = { operationId: string; body: string };

function readPending(): PendingTemplate | null {
  const value = localStorage.getItem(PENDING_KEY);
  if (!value) return null;
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending endgame admission is invalid. Restore browser data before retrying.");
  return parsed as PendingTemplate;
}

export async function admitEndgameTemplate(request: TemplateRequest) {
  const body = JSON.stringify(request);
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier endgame admission failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.body === body) return endgameCreatedSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/endgames/templates`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not add this material set to training.");
  const result = await readJsonResponse(response, endgameCreatedSchema, "endgame admission");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
