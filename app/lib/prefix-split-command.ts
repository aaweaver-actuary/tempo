import { readPendingPrefixSplitRecord, writePendingPrefixSplitRecord, clearPendingPrefixSplitRecord } from "../../database/browser/adapters/authoring/prefix-split";
import { readPendingCardDeletionBytes } from "../../database/browser/adapters/authoring/card-delete";
import { z } from "zod";
import { API_URL } from "../const";
import { prefixSplitResponseSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const rejectionSchema = z.strictObject({ rejected_after_review_id: z.number().int() });
type Decision = "accept" | "reject";
type PendingDecision = { operationId: string; decision: Decision; cardId: string; revision: number; queueEntryId?: number };

function readPending(): PendingDecision | null {
  const parsed = readPendingPrefixSplitRecord();
  if (parsed === undefined) return null;
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("decision" in parsed) || (parsed.decision !== "accept" && parsed.decision !== "reject") ||
      !("cardId" in parsed) || typeof parsed.cardId !== "string" ||
      !("revision" in parsed) || !Number.isInteger(parsed.revision) ||
      ("queueEntryId" in parsed && parsed.queueEntryId !== undefined &&
        (!Number.isInteger(parsed.queueEntryId) || Number(parsed.queueEntryId) < 1)))
    throw new Error("The pending prefix split is invalid. Restore browser data before retrying.");
  return parsed as PendingDecision;
}

async function decidePrefixSplit(cardId: string, revision: number, decision: Decision, queueEntryId?: number) {
  if (readPendingCardDeletionBytes())
    throw new Error("Resolve the pending card deletion before editing a card.");
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as { state?: string; response?: unknown; error?: { message?: string } };
    if (receipt.state === "failed") {
      clearPendingPrefixSplitRecord();
      throw new Error(receipt.error?.message ?? "The earlier prefix split decision failed.");
    }
    if (receipt.state === "complete") {
      clearPendingPrefixSplitRecord();
      if (pending.cardId === cardId && pending.revision === revision && pending.decision === decision && pending.queueEntryId === queueEntryId)
        return decision === "accept"
          ? prefixSplitResponseSchema.parse(receipt.response)
          : rejectionSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.cardId !== cardId || pending.revision !== revision || pending.decision !== decision || pending.queueEntryId !== queueEntryId)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), decision, cardId, revision, ...(queueEntryId === undefined ? {} : { queueEntryId }) };
    writePendingPrefixSplitRecord(pending);
  }
  let response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(cardId)}/prefix-split${decision === "reject" ? "/reject" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ expected_revision: revision, ...(pending.queueEntryId === undefined ? {} : { queue_entry_id: pending.queueEntryId }) }),
  });
  response = await confirmOperationResponse(response);
  const result = decision === "accept"
    ? await readJsonResponse(response, prefixSplitResponseSchema, "accepted prefix split")
    : await readJsonResponse(response, rejectionSchema, "rejected prefix split");
  clearPendingPrefixSplitRecord();
  return result;
}

export async function acceptPrefixSplitCommand(cardId: string, revision: number, queueEntryId?: number) {
  return prefixSplitResponseSchema.parse(await decidePrefixSplit(cardId, revision, "accept", queueEntryId));
}

export async function rejectPrefixSplitCommand(cardId: string, revision: number) {
  return rejectionSchema.parse(await decidePrefixSplit(cardId, revision, "reject"));
}
