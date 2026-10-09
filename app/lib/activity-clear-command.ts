import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, readOperationResponse } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const pendingKey = "tempo-pending-activity-clear-v1";
const snapshotSchema = z.strictObject({ completion_cutoff: z.string().datetime({ offset: true }), completion_snapshot: z.string().min(1) });
const pendingSchema = snapshotSchema.extend({ operationId: z.string().min(1) });
const resultSchema = z.strictObject({ ok: z.literal(true), cleared_through: z.string().datetime({ offset: true }) });

export async function clearFinishedActivity(snapshot: z.infer<typeof snapshotSchema>): Promise<void> {
  const stored = localStorage.getItem(pendingKey);
  let pending = stored ? pendingSchema.parse(JSON.parse(stored)) : null;
  const accept = async (response: Response) => {
    const result = await readJsonResponse(response, resultSchema, "clear finished activity");
    if (!pending || Date.parse(result.cleared_through) < Date.parse(pending.completion_cutoff))
      throw new Error("Finished activity was not confirmed. Refresh and check its status.");
    localStorage.removeItem(pendingKey);
  };
  if (pending) {
    try {
      const receipt = await readOperationResponse(pending.operationId, { allowMissing: true });
      if (receipt.status !== 404) { await accept(receipt); return; }
    } catch (error) {
      if (error instanceof FailedOperationError) localStorage.removeItem(pendingKey);
      throw error;
    }
  } else {
    pending = { ...snapshotSchema.parse(snapshot), operationId: crypto.randomUUID() };
    localStorage.setItem(pendingKey, JSON.stringify(pending));
  }
  const { operationId, ...originalSnapshot } = pending;
  const response = await fetch(`${API_URL}/api/system/activity/clear-finished`, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": operationId },
    body: JSON.stringify(originalSnapshot),
  });
  try { await accept(await confirmOperationResponse(response)); }
  catch (error) {
    if (error instanceof FailedOperationError || (response.status >= 400 && response.status < 500))
      localStorage.removeItem(pendingKey);
    throw error;
  }
}
