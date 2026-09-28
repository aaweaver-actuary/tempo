import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-game-exclusion-v1:";
const savedResult = z.strictObject({ game_id: z.string(), excluded: z.boolean() });
type PendingExclusion = { operationId: string; excluded: boolean };

function readPending(storageKey: string): PendingExclusion | null {
  const saved = localStorage.getItem(storageKey);
  if (!saved) return null;
  const parsed: unknown = JSON.parse(saved);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("excluded" in parsed) || typeof parsed.excluded !== "boolean")
    throw new Error("The pending game decision is invalid. Restore browser data before retrying.");
  return parsed as PendingExclusion;
}

export async function setGameExclusion(gameId: string, excluded: boolean): Promise<void> {
  const storageKey = `${PENDING_PREFIX}${gameId}`;
  let pending = readPending(storageKey);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      const result = savedResult.parse(receipt.response);
      localStorage.removeItem(storageKey);
      if (result.game_id === gameId && result.excluded === excluded) return;
      pending = null;
    } else if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "The earlier game decision failed.");
    } else if (receipt.state === "pending") {
      if (pending.excluded !== excluded) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), excluded };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/games/${encodeURIComponent(gameId)}/exclusion`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ excluded }),
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, savedResult, "save game exclusion");
  if (result.game_id !== gameId || result.excluded !== excluded)
    throw new Error("The game decision response did not match the request.");
  localStorage.removeItem(storageKey);
}
