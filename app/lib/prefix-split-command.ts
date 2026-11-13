import { decodeOperationResult } from "../domain/adapters/operation-result-adapter";
import { readPendingPrefixSplitRecord, writePendingPrefixSplitRecord, clearPendingPrefixSplitRecord, clearPendingPrefixSplitIfMatches, attachPrefixSplitIntentIfMatches } from "../../database/browser/adapters/authoring/prefix-split";
import { readPendingCardDeletionBytes } from "../../database/browser/adapters/authoring/card-delete";
import { z } from "zod";
import { API_URL } from "../const";
import { prefixSplitResponseSchema } from "../domain/schemas";
import { confirmOriginalOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const rejectionSchema = z.strictObject({ rejected_after_review_id: z.number().int() });
type Decision = "accept" | "reject";
type PendingDecision = { operationId: string; decision: Decision; cardId: string; revision: number; queueEntryId?: number; intentId?: string };
const deferredSchema = z.strictObject({ status: z.enum(["waiting_review", "ready", "held"]), intent_id: z.string().min(1),
  card_id: z.string(), source_revision: z.number().int().positive(), queue_entry_id: z.number().int().positive(), reason: z.string().nullable() });
const intentStatusSchema = z.strictObject({ status: z.enum(["waiting_review", "ready", "held", "applied"]), intent_id: z.string().min(1),
  card_id: z.string(), source_revision: z.number().int().positive(), queue_entry_id: z.number().int().positive(),
  reason: z.string().nullable(), result: prefixSplitResponseSchema.nullable() });
type DeferredSplit = z.infer<typeof deferredSchema>;

function validateDeferred(value: unknown, pending: PendingDecision): DeferredSplit {
  const result = deferredSchema.parse(value);
  if (result.card_id !== pending.cardId || result.source_revision !== pending.revision ||
      result.queue_entry_id !== pending.queueEntryId || (pending.intentId && result.intent_id !== pending.intentId))
    throw new Error("The split intent does not match the original attempt.");
  return result;
}

function validateAccept(value: unknown, pending: PendingDecision) {
  const deferred = deferredSchema.safeParse(value);
  if (deferred.success) return validateDeferred(deferred.data, pending);
  const result = prefixSplitResponseSchema.parse(value);
  if (result.source_card_id !== pending.cardId || result.source_revision !== pending.revision)
    throw new Error("The split result does not match the original card revision.");
  return result;
}

function readPending(): PendingDecision | null {
  const parsed = readPendingPrefixSplitRecord();
  if (parsed === undefined) return null;
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("decision" in parsed) || (parsed.decision !== "accept" && parsed.decision !== "reject") ||
      !("cardId" in parsed) || typeof parsed.cardId !== "string" ||
      !("revision" in parsed) || !Number.isInteger(parsed.revision) ||
      ("queueEntryId" in parsed && parsed.queueEntryId !== undefined &&
        (!Number.isInteger(parsed.queueEntryId) || Number(parsed.queueEntryId) < 1)) ||
      ("intentId" in parsed && parsed.intentId !== undefined && typeof parsed.intentId !== "string"))
    throw new Error("The pending prefix split is invalid. Restore browser data before retrying.");
  return parsed as PendingDecision;
}

async function decidePrefixSplit(cardId: string, revision: number, decision: Decision, queueEntryId?: number) {
  if (readPendingCardDeletionBytes())
    throw new Error("Resolve the pending card deletion before editing a card.");
  let pending = readPending();
  if (pending) {
    if (pending.intentId) {
      if (pending.cardId !== cardId || pending.revision !== revision || pending.decision !== decision || pending.queueEntryId !== queueEntryId)
        throw new PendingOperationError(pending.operationId);
      return readSplitIntent(pending);
    }
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = decodeOperationResult(await status.json(), pending.operationId, false);
    if (receipt.kind === "failed") {
      clearPendingPrefixSplitRecord();
      throw new Error(receipt.error.message ?? "The earlier prefix split decision failed.");
    }
    if (receipt.kind === "complete") {
      const result = pending.decision === "accept"
        ? validateAccept(receipt.response, pending)
        : rejectionSchema.parse(receipt.response);
      if ("status" in result) {
        if (!attachPrefixSplitIntentIfMatches(pending.operationId, result.intent_id))
          throw new Error("The pending split changed while its intent was confirmed.");
        if (pending.cardId !== cardId || pending.revision !== revision || pending.decision !== decision || pending.queueEntryId !== queueEntryId)
          throw new PendingOperationError(pending.operationId);
        return result;
      }
      clearPendingPrefixSplitRecord();
      if (pending.cardId === cardId && pending.revision === revision && pending.decision === decision && pending.queueEntryId === queueEntryId)
        return result;
      pending = null;
    } else if (receipt.kind === "pending") {
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
  response = await confirmOriginalOperationResponse(response, pending.operationId);
  const result = decision === "accept"
    ? validateAccept(await readJsonResponse(response, z.unknown(), "accepted prefix split"), pending)
    : await readJsonResponse(response, rejectionSchema, "rejected prefix split");
  if ("status" in result) {
    if (!attachPrefixSplitIntentIfMatches(pending.operationId, result.intent_id))
      throw new Error("The pending split changed while its intent was confirmed.");
    return result;
  }
  clearPendingPrefixSplitRecord();
  return result;
}

export async function acceptPrefixSplitCommand(cardId: string, revision: number, queueEntryId?: number) {
  return await decidePrefixSplit(cardId, revision, "accept", queueEntryId) as z.infer<typeof prefixSplitResponseSchema> | DeferredSplit;
}

export async function rejectPrefixSplitCommand(cardId: string, revision: number) {
  return rejectionSchema.parse(await decidePrefixSplit(cardId, revision, "reject"));
}

async function readSplitIntent(pending: PendingDecision) {
  const response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(pending.cardId)}/prefix-split/intents/${encodeURIComponent(pending.intentId!)}`);
  const status = await readJsonResponse(response, intentStatusSchema, "prefix split status");
  if (status.intent_id !== pending.intentId || status.card_id !== pending.cardId ||
      status.source_revision !== pending.revision || status.queue_entry_id !== pending.queueEntryId)
    throw new Error("The split status does not match the original attempt.");
  if (status.status === "applied") {
    const result = validateAccept(status.result, pending);
    if ("status" in result) throw new Error("The completed split has no result.");
    clearPendingPrefixSplitIfMatches(pending.operationId, pending.intentId!);
    return result;
  }
  if (status.result !== null) throw new Error("The pending split has an unexpected result.");
  return validateDeferred({ status: status.status, intent_id: status.intent_id, card_id: status.card_id,
    source_revision: status.source_revision, queue_entry_id: status.queue_entry_id, reason: status.reason }, pending);
}

/** Inspect an already accepted intent without replaying or inventing a command. */
export async function pendingPrefixSplitStatus(cardId?: string, queueEntryId?: number) {
  const pending = readPending();
  if (pending && cardId !== undefined && (pending.cardId !== cardId || pending.queueEntryId !== queueEntryId)) return undefined;
  return pending?.intentId ? readSplitIntent(pending) : undefined;
}
