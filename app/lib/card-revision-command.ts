import { API_URL } from "../const";
import { cardRevisionResultSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-card-revision-v1";
type CardRevision = {
  cardId: string;
  startingFen: string;
  moves: string[];
  historyMode: "preserve" | "reset";
  expectedRevision: number;
};
type PendingRevision = { operationId: string; request: string };

function readPending(): PendingRevision | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("request" in parsed) || typeof parsed.request !== "string")
    throw new Error("The pending card revision is invalid. Restore browser data before retrying.");
  return parsed as PendingRevision;
}

export async function reviseCardCommand(revision: CardRevision) {
  const request = JSON.stringify(revision);
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier card revision failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.request === request) return cardRevisionResultSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.request !== request) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), request };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(revision.cardId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({
      starting_fen: revision.startingFen, moves: revision.moves,
      history_mode: revision.historyMode, expected_revision: revision.expectedRevision,
    }),
  });
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, cardRevisionResultSchema, "card revision");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
