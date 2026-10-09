import { z } from "zod";
import { ensureExplorerSession } from "./explorer-session";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-coverage-refresh-v1:";
const queuedResult = z.strictObject({ run_id: z.string(), status: z.literal("queued") });

export async function requestCoverageRefresh(repertoireId: string): Promise<void> {
  // Explorer registration is independent of the useful Maia coverage attempt.
  await ensureExplorerSession().catch(() => undefined);
  const pendingKey = `${PENDING_PREFIX}${repertoireId}`;
  let operationId = localStorage.getItem(pendingKey);
  if (operationId) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
    if (!status.ok) throw new PendingOperationError(operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      queuedResult.parse(receipt.response);
      localStorage.removeItem(pendingKey);
      return;
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(pendingKey);
      throw new Error(receipt.error?.message ?? "The coverage refresh failed.");
    }
    if (receipt.state !== "pending") throw new PendingOperationError(operationId);
  } else {
    operationId = crypto.randomUUID();
    localStorage.setItem(pendingKey, operationId);
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/coverage/refresh`,
    { method: "POST", headers: { "Idempotency-Key": operationId } },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, queuedResult, "refresh coverage");
  queuedResult.parse(result);
  localStorage.removeItem(pendingKey);
}
