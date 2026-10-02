import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const dailyLimitSchema = z.number().int().min(0).max(100);
export const repertoireSettingsResponseSchema = z.strictObject({
  repertoire_id: z.string().min(1),
  new_cards_per_day: dailyLimitSchema.nullable(),
  effective_new_cards_per_day: dailyLimitSchema,
});
const pendingSchema = z.strictObject({ operationId: z.uuid(), body: z.string() });
const requestSchema = z.strictObject({ new_cards_per_day: dailyLimitSchema.nullable() });
const pendingKey = (repertoireId: string) => `tempo-pending-repertoire-settings-v1:${repertoireId}`;

function readPending(repertoireId: string) {
  const stored = localStorage.getItem(pendingKey(repertoireId));
  if (!stored) return null;
  try {
    const pending = pendingSchema.parse(JSON.parse(stored));
    requestSchema.parse(JSON.parse(pending.body));
    return pending;
  } catch { throw new Error("The pending repertoire save is unreadable. Restore browser data before retrying."); }
}

export function pendingRepertoireLimit(repertoireId: string): { new_cards_per_day: number | null } | null {
  const pending = readPending(repertoireId);
  return pending ? requestSchema.parse(JSON.parse(pending.body)) : null;
}

export async function saveRepertoireLimit(repertoireId: string, limit: number | null) {
  let pending = readPending(repertoireId);
  function validateResult(raw: unknown) {
    const result = repertoireSettingsResponseSchema.parse(raw);
    if (result.repertoire_id !== repertoireId || result.new_cards_per_day !== JSON.parse(pending!.body).new_cards_per_day)
      throw new Error("The service returned different repertoire settings. Retry to confirm this save.");
    localStorage.removeItem(pendingKey(repertoireId));
    return result;
  }
  if (pending) {
    let receipt: { state?: string; response?: unknown; error?: { message?: string } } = {};
    try {
      const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
      if (status.ok) receipt = await status.json();
    } catch { /* Replaying the saved identity and bytes is safe after a lost receipt. */ }
    if (receipt.state === "complete") return validateResult(receipt.response);
    if (receipt.state === "failed") {
      localStorage.removeItem(pendingKey(repertoireId));
      throw new FailedOperationError(receipt.error?.message ?? "The repertoire save failed.", pending.operationId);
    }
    if (receipt.state === "blocked") throw new PendingOperationError(pending.operationId, "The repertoire save is blocked. Resolve its operation error before retrying.", true);
  } else {
    const request = requestSchema.parse({ new_cards_per_day: limit });
    pending = { operationId: crypto.randomUUID(), body: JSON.stringify(request) };
    localStorage.setItem(pendingKey(repertoireId), JSON.stringify(pending));
  }
  try {
    let response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/settings`, {
      method: "PUT", headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId }, body: pending.body,
    });
    if ([400, 404, 409, 422].includes(response.status)) {
      localStorage.removeItem(pendingKey(repertoireId));
      await readJsonResponse(response, repertoireSettingsResponseSchema, "repertoire settings");
    }
    response = await confirmOperationResponse(response);
    return validateResult(await readJsonResponse(response, repertoireSettingsResponseSchema, "repertoire settings"));
  } catch (error) {
    if (error instanceof FailedOperationError) localStorage.removeItem(pendingKey(repertoireId));
    throw error;
  }
}
