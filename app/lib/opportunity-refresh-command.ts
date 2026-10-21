import { readPendingOpportunityRefreshIdentity, writePendingOpportunityRefreshIdentity, clearPendingOpportunityRefreshIdentity } from "../../database/browser/adapters/authoring/opportunity-refresh";
import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const queuedResult = z.strictObject({ queued: z.literal(true) });

export async function requestOpportunityRefresh(repertoireId: string): Promise<void> {
  let operationId = readPendingOpportunityRefreshIdentity(repertoireId);
  if (operationId) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
    if (!status.ok) throw new PendingOperationError(operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      queuedResult.parse(receipt.response);
      clearPendingOpportunityRefreshIdentity(repertoireId);
      return;
    }
    if (receipt.state === "failed") {
      clearPendingOpportunityRefreshIdentity(repertoireId);
      throw new Error(receipt.error?.message ?? "The discovery refresh failed.");
    }
    if (receipt.state !== "pending") throw new PendingOperationError(operationId);
  } else {
    operationId = crypto.randomUUID();
    writePendingOpportunityRefreshIdentity(repertoireId, operationId);
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/opportunities/refresh`,
    { method: "POST", headers: { "Idempotency-Key": operationId } },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, queuedResult, "refresh discoveries");
  queuedResult.parse(result);
  clearPendingOpportunityRefreshIdentity(repertoireId);
}
