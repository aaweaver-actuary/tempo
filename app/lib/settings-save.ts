import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";

const pendingKey = "tempo-pending-settings-save-v1";
type PendingSettingsSave = { operationId: string; body: string };

function pendingSave(): PendingSettingsSave | null {
  const stored = localStorage.getItem(pendingKey);
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
  if (pending && pending.body !== body) {
    const response = await fetch(
      `${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`,
    );
    if (!response.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await response.json() as { state?: string; error?: { message?: string } };
    if (receipt.state === "pending") throw new PendingOperationError(pending.operationId);
    if (receipt.state === "failed") {
      localStorage.removeItem(pendingKey);
      throw new Error(receipt.error?.message ?? "The earlier settings save failed.");
    }
    if (receipt.state !== "complete") throw new PendingOperationError(pending.operationId);
    localStorage.removeItem(pendingKey);
    pending = null;
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(pendingKey, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": pending.operationId,
    },
    body,
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) {
    const result = await response.json().catch(() => ({})) as { detail?: string };
    // A failure between broker acceptance and receipt lookup can look like
    // HTTP 503. Keep the operation ID until a receipt proves its state.
    throw new Error(result.detail ?? `Settings save returned HTTP ${response.status}.`);
  }
  localStorage.removeItem(pendingKey);
}
