import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const sessionResult = z.object({
  id: z.string(), game_id: z.string(), status: z.enum(["active", "complete"]),
  current_index: z.number().int(), total: z.number().int(),
  current: z.unknown(), attempts: z.array(z.unknown()),
});
const attemptResult = z.object({
  correct: z.boolean(), revealed: z.unknown(), session: sessionResult,
});

type PendingAttempt = { operationId: string; moveUci: string };

function readPendingAttempt(storageKey: string): PendingAttempt | null {
  const saved = localStorage.getItem(storageKey);
  if (!saved) return null;
  const parsed: unknown = JSON.parse(saved);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("moveUci" in parsed) || typeof parsed.moveUci !== "string")
    throw new Error("The pending guided review is invalid. Restore browser data before retrying.");
  return parsed as PendingAttempt;
}

export async function startGuidedReviewCommand(gameId: string) {
  const storageKey = `tempo-pending-guided-review-start-v1:${gameId}`;
  let operationId = localStorage.getItem(storageKey);
  if (operationId) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(operationId)}`);
    if (!status.ok) throw new PendingOperationError(operationId);
    const receipt = await status.json() as { state?: string; response?: unknown; error?: { message?: string } };
    if (receipt.state === "complete") {
      localStorage.removeItem(storageKey);
      return sessionResult.parse(receipt.response);
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "Guided review could not start.");
    }
    if (receipt.state !== "pending") throw new PendingOperationError(operationId);
  } else {
    operationId = crypto.randomUUID();
    localStorage.setItem(storageKey, operationId);
  }
  let response = await fetch(
    `${API_URL}/api/games/${encodeURIComponent(gameId)}/guided-review`,
    { method: "POST", headers: { "Idempotency-Key": operationId } },
  );
  response = await confirmOperationResponse(response);
  const session = await readJsonResponse(response, sessionResult, "start guided review");
  if (session.game_id !== gameId) throw new Error("Guided review response did not match the game.");
  localStorage.removeItem(storageKey);
  return session;
}

export async function submitGuidedReviewCommand(
  sessionId: string, currentIndex: number, moveUci: string,
) {
  const storageKey = `tempo-pending-guided-review-attempt-v1:${sessionId}:${currentIndex}`;
  let pending = readPendingAttempt(storageKey);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as { state?: string; response?: unknown; error?: { message?: string } };
    if (receipt.state === "complete") {
      localStorage.removeItem(storageKey);
      return attemptResult.parse(receipt.response);
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "Correction attempt could not be saved.");
    }
    if (receipt.state !== "pending" || pending.moveUci !== moveUci)
      throw new PendingOperationError(pending.operationId);
  } else {
    pending = { operationId: crypto.randomUUID(), moveUci };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/guided-reviews/${encodeURIComponent(sessionId)}/attempt`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ move_uci: moveUci }),
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, attemptResult, "save guided correction");
  if (result.session.id !== sessionId || result.session.current_index !== currentIndex + 1)
    throw new Error("Guided review response did not match the attempt.");
  localStorage.removeItem(storageKey);
  return result;
}
