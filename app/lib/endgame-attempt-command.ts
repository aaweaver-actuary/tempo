import { LEARNING_URL as API_URL, learningFetch as fetch, independentStudy } from "./study-connection";
import { endgameAttemptSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";
import { executeStudyRequest, retainedStudyRequest } from "./study-command-client";

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
  if (independentStudy) {
    const scope = `endgame:${templateId}`;
    const retained = await retainedStudyRequest(scope);
    const requestId = retained && retained.state !== "committed" ? retained.requestId : crypto.randomUUID();
    const response = await executeStudyRequest({ scope, kind: "endgame-command", requestId,
      endpoint: `${API_URL}/api/endgames/templates/${encodeURIComponent(templateId)}/attempt`, body: {} });
    return endgameAttemptSchema.parse(await response.json());
  }
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
