import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-game-threat-refresh-v1:";
const queuedResult = z.strictObject({ status: z.literal("queued"), analysis_version: z.number().int() });

export async function requestGameThreatRefresh(gameId: string): Promise<void> {
  const storageKey = `${PENDING_PREFIX}${gameId}`;
  let operationId = localStorage.getItem(storageKey);
  if (operationId) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
    if (!status.ok) throw new PendingOperationError(operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      queuedResult.parse(receipt.response);
      localStorage.removeItem(storageKey);
      return;
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "The earlier defensive scan request failed.");
    }
    if (receipt.state !== "pending") throw new PendingOperationError(operationId);
  } else {
    operationId = crypto.randomUUID();
    localStorage.setItem(storageKey, operationId);
  }
  let response = await fetch(
    `${API_URL}/api/games/${encodeURIComponent(gameId)}/defensive-threats/refresh`,
    { method: "POST", headers: { "Idempotency-Key": operationId } },
  );
  response = await confirmOperationResponse(response);
  await readJsonResponse(response, queuedResult, "queue defensive analysis");
  localStorage.removeItem(storageKey);
}
