import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-activity-control-v1:";
const resultSchema = z.strictObject({ ok: z.literal(true) });
type PendingControl = { operationId: string; action: string };

export async function requestActivityControl(source: string, workId: string, action: string): Promise<void> {
  const pendingKey = `${PENDING_PREFIX}${source}:${workId}`;
  const stored = localStorage.getItem(pendingKey);
  let pending: PendingControl | null = stored ? JSON.parse(stored) as PendingControl : null;
  if (pending) {
    if (typeof pending.operationId !== "string" || typeof pending.action !== "string")
      throw new Error("Pending activity control is invalid. Restore browser data before retrying.");
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(pendingKey);
      throw new Error(receipt.error?.message ?? "The earlier activity control failed.");
    }
    if (receipt.state === "complete") {
      resultSchema.parse(receipt.response);
      localStorage.removeItem(pendingKey);
      if (pending.action === action) return;
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.action !== action) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), action };
    localStorage.setItem(pendingKey, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/system/activity/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ source, id: workId, action }),
  });
  response = await confirmOperationResponse(response);
  await readJsonResponse(response, resultSchema, "control background activity");
  localStorage.removeItem(pendingKey);
}
