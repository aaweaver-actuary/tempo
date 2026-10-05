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
  correct: z.boolean(), revealed: z.object({ finding_id: z.string() }).passthrough(), session: sessionResult,
});

export class GuidedReviewChangedError extends Error {
  constructor(message = "Guided review changed. Reloading the current finding.") {
    super(message);
    this.name = "GuidedReviewChangedError";
  }
}

export async function readGuidedReviewCommand(sessionId: string) {
  const response = await fetch(`${API_URL}/api/guided-reviews/${encodeURIComponent(sessionId)}`);
  const session = await readJsonResponse(response, sessionResult, "reload guided review");
  if (session.id !== sessionId) throw new Error("Guided review response did not match the session.");
  return session;
}

const rejectedAttempt = z.object({ guided_review_error: z.object({ status_code: z.number(), detail: z.string() }) });
function parseAttemptResult(value: unknown, sessionId: string, findingId: string) {
  const rejected = rejectedAttempt.safeParse(value);
  if (rejected.success) throw new GuidedReviewChangedError(rejected.data.guided_review_error.detail);
  const result = attemptResult.parse(value);
  if (result.session.id !== sessionId || result.revealed.finding_id !== findingId)
    throw new GuidedReviewChangedError("Guided review response did not match the displayed finding.");
  return result;
}

type PendingAttempt = { operationId: string; moveUci: string; findingId?: string };

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
  sessionId: string, currentIndex: number, moveUci: string, findingId: string,
) {
  const legacyStorageKey = `tempo-pending-guided-review-attempt-v1:${sessionId}:${currentIndex}`;
  const storageKey = localStorage.getItem(legacyStorageKey) ? legacyStorageKey
    : `tempo-pending-guided-review-attempt-v2:${sessionId}:${findingId}`;
  let pending = readPendingAttempt(storageKey);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as { state?: string; response?: unknown; error?: { message?: string } };
    if (receipt.state === "complete") {
      localStorage.removeItem(storageKey);
      return parseAttemptResult(receipt.response, sessionId, findingId);
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "Correction attempt could not be saved.");
    }
    if (receipt.state !== "pending" || pending.moveUci !== moveUci || pending.findingId !== findingId)
      throw new PendingOperationError(pending.operationId,
        "The earlier correction is unresolved. Check its operation before submitting another finding.");
  } else {
    pending = { operationId: crypto.randomUUID(), moveUci, findingId };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/guided-reviews/${encodeURIComponent(sessionId)}/attempt`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ move_uci: pending.moveUci, finding_id: pending.findingId }),
  });
  response = await confirmOperationResponse(response);
  if (response.status === 404 || response.status === 409) {
    localStorage.removeItem(storageKey);
    throw new GuidedReviewChangedError();
  }
  if (!response.ok) return readJsonResponse(response, attemptResult, "save guided correction");
  const value: unknown = await response.json();
  // A committed rejection may arrive as an operation receipt after HTTP 202.
  if (rejectedAttempt.safeParse(value).success) localStorage.removeItem(storageKey);
  const result = parseAttemptResult(value, sessionId, findingId);
  localStorage.removeItem(storageKey);
  return result;
}
