import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-opportunity-v1:";
type Action = "dismiss" | "acknowledge" | "snooze";
type PendingAction = { operationId: string; repertoireId: string; action: Action };
const responseSchemas = {
  dismiss: z.strictObject({ dismissed: z.literal(true) }),
  acknowledge: z.strictObject({ acknowledged: z.literal(true) }),
  snooze: z.strictObject({ snoozed: z.literal(true) }),
};

function readPending(key: string): PendingAction | null {
  const stored = localStorage.getItem(key);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string" ||
      !("action" in parsed) || !["dismiss", "acknowledge", "snooze"].includes(String(parsed.action)))
    throw new Error("The pending discovery action is invalid. Restore browser data before retrying.");
  return parsed as PendingAction;
}

function validateResult(action: Action, value: unknown): void {
  responseSchemas[action].parse(value);
}

export async function applyOpportunityCommand(
  repertoireId: string, opportunityId: string, action: Action,
): Promise<void> {
  const key = `${PENDING_PREFIX}${opportunityId}`;
  let pending = readPending(key);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(key);
      throw new Error(receipt.error?.message ?? "The earlier discovery action failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(key);
      if (pending.repertoireId === repertoireId && pending.action === action) {
        validateResult(action, receipt.response);
        return;
      }
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.repertoireId !== repertoireId || pending.action !== action)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId, action };
    localStorage.setItem(key, JSON.stringify(pending));
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/opportunities/${encodeURIComponent(opportunityId)}/${action}`,
    { method: "POST", headers: { "Idempotency-Key": pending.operationId } },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, z.unknown(), `${action} discovery`);
  validateResult(action, result);
  localStorage.removeItem(key);
}
