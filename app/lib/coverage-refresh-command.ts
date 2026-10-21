import { readPendingCoverageRefreshIdentity, writePendingCoverageRefreshIdentity, clearPendingCoverageRefreshIdentity } from "../../database/browser/adapters/authoring/coverage-refresh";
import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const queuedResult = z.strictObject({ run_id: z.string(), status: z.literal("queued") });

export async function requestCoverageRefresh(repertoireId: string): Promise<void> {
  let operationId = readPendingCoverageRefreshIdentity(repertoireId);
  if (operationId) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
    if (!status.ok) throw new PendingOperationError(operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      queuedResult.parse(receipt.response);
      clearPendingCoverageRefreshIdentity(repertoireId);
      return;
    }
    if (receipt.state === "failed") {
      clearPendingCoverageRefreshIdentity(repertoireId);
      throw new Error(receipt.error?.message ?? "The coverage refresh failed.");
    }
    if (receipt.state !== "pending") throw new PendingOperationError(operationId);
  } else {
    operationId = crypto.randomUUID();
    writePendingCoverageRefreshIdentity(repertoireId, operationId);
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/coverage/refresh`,
    { method: "POST", headers: { "Idempotency-Key": operationId } },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, queuedResult, "refresh coverage");
  queuedResult.parse(result);
  clearPendingCoverageRefreshIdentity(repertoireId);
}
