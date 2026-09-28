import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";

const pendingKey = "tempo-pending-game-sync-command-v1";
type PendingSync = { operationId: string; body: string };

function pendingSync(): PendingSync | null {
  const raw = localStorage.getItem(pendingKey);
  if (!raw) return null;
  const parsed: unknown = JSON.parse(raw);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending game sync is invalid. Restore browser data before retrying.");
  return parsed as PendingSync;
}

export function hasPendingGameSyncCommand(): boolean {
  return pendingSync() !== null;
}

export async function enqueueGameSyncCommand(
  payload: Record<string, unknown>,
  requester: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
): Promise<Response> {
  const body = JSON.stringify(payload);
  let pending = pendingSync();
  if (pending) {
    const response = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!response.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await response.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "pending" && pending.body !== body)
      throw new PendingOperationError(pending.operationId);
    if (receipt.state === "failed") {
      localStorage.removeItem(pendingKey);
      throw new Error(receipt.error?.message ?? "The earlier game sync request failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(pendingKey);
      if (pending.body === body) return Response.json(receipt.response, { status: 202 });
      pending = null;
    } else if (receipt.state !== "pending") {
      throw new PendingOperationError(pending.operationId);
    }
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(pendingKey, JSON.stringify(pending));
  }
  let response = await requester(`${API_URL}/api/games/sync`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": pending.operationId,
    },
    body,
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) {
    const result = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(result.detail ?? `Game sync returned HTTP ${response.status}.`);
  }
  localStorage.removeItem(pendingKey);
  return response;
}
