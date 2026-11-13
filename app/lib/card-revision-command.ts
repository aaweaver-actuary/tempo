import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { readPendingCardRevisionRecord, writePendingCardRevisionRecord, clearPendingCardRevisionRecord } from "../../database/browser/adapters/authoring/card-revision";
import { readPendingCardDeletionBytes } from "../../database/browser/adapters/authoring/card-delete";
import { API_URL } from "../const";
import { cardRevisionResultSchema } from "../domain/schemas";
import { confirmOriginalOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

type CardRevision = {
  cardId: string;
  startingFen: string;
  moves: string[];
  historyMode: "preserve" | "reset";
  expectedRevision: number;
};
type PendingRevision = { operationId: string; request: string };

function readPending(): PendingRevision | null {
  const parsed = readPendingCardRevisionRecord();
  if (parsed === undefined) return null;
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("request" in parsed) || typeof parsed.request !== "string")
    throw new Error("The pending card revision is invalid. Restore browser data before retrying.");
  return parsed as PendingRevision;
}

export async function reviseCardCommand(revision: CardRevision) {
  if (readPendingCardDeletionBytes())
    throw new Error("Resolve the pending card deletion before editing a card.");
  const request = JSON.stringify(revision);
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = decodeOperationResult(await status.json(), pending.operationId, false);
    if (receipt.kind === "failed") {
      clearPendingCardRevisionRecord();
      throw new Error(receipt.error.message ?? "The earlier card revision failed.");
    }
    if (receipt.kind === "complete") {
      const result = cardRevisionResultSchema.parse(receipt.response);
      clearPendingCardRevisionRecord();
      if (pending.request === request) return result;
      pending = null;
    } else if (receipt.kind === "pending") {
      if (pending.request !== request) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), request };
    writePendingCardRevisionRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(revision.cardId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({
      starting_fen: revision.startingFen, moves: revision.moves,
      history_mode: revision.historyMode, expected_revision: revision.expectedRevision,
    }),
  });
  response = await confirmOriginalOperationResponse(response, pending.operationId);
  const result = await readJsonResponse(response, cardRevisionResultSchema, "card revision");
  clearPendingCardRevisionRecord();
  return result;
}
