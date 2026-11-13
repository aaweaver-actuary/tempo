import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { settingsResponseSchema } from "../domain/schemas";
import { readPendingSettingsSave, writePendingSettingsSave, removePendingSettingsSave } from "../../database/browser/adapters/practice/settings-save";
import { API_URL } from "../const";
import { confirmOriginalOperationResponse, PendingOperationError } from "./operation-status";

type PendingSettingsSave = { operationId: string; body: string };

function pendingSave(): PendingSettingsSave | null {
  const stored = readPendingSettingsSave();
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending settings save is invalid. Restore browser data before retrying.");
  return parsed as PendingSettingsSave;
}

export async function saveLocalSettings(settings: Record<string, unknown>): Promise<void> {
  const body = JSON.stringify(settings);
  let pending = pendingSave();
  if (pending) {
    const response = await fetch(
      `${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`,
    );
    if (!response.ok) throw new PendingOperationError(pending.operationId);
    const receipt = decodeOperationResult(await response.json(), pending.operationId, false);
    if (receipt.kind === "pending" && pending.body !== body)
      throw new PendingOperationError(pending.operationId);
    if (receipt.kind === "failed") {
      removePendingSettingsSave();
      throw new Error(receipt.error.message ?? "The earlier settings save failed.");
    }
    if (receipt.kind === "complete") {
      if (!settingsResponseSchema.safeParse(receipt.response).success) throw new PendingOperationError(pending.operationId);
      removePendingSettingsSave();
      if (pending.body === body) return;
      pending = null;
    } else if (receipt.kind !== "pending") {
      throw new PendingOperationError(pending.operationId);
    }
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    writePendingSettingsSave(pending);
  }
  let response = await fetch(`${API_URL}/api/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": pending.operationId,
    },
    body,
  });
  response = await confirmOriginalOperationResponse(response, pending.operationId);
  if (!response.ok) {
    const result = await response.json().catch(() => ({})) as { detail?: string };
    // A failure between broker acceptance and receipt lookup can look like
    // HTTP 503. Keep the operation ID until a receipt proves its state.
    throw new Error(result.detail ?? `Settings save returned HTTP ${response.status}.`);
  }
  const result: unknown = await response.json().catch(() => undefined);
  if (!settingsResponseSchema.safeParse(result).success) throw new PendingOperationError(pending.operationId);
  removePendingSettingsSave();
}
