import { z } from "zod";
import { API_URL } from "../const";
import { prefixSplitResponseSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-prefix-split-v1";
const rejectionSchema = z.strictObject({ rejected_after_review_id: z.number().int() });
type Decision = "accept" | "reject";
type PendingDecision = { operationId: string; decision: Decision; cardId: string; revision: number };

function readPending(): PendingDecision | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("decision" in parsed) || (parsed.decision !== "accept" && parsed.decision !== "reject") ||
      !("cardId" in parsed) || typeof parsed.cardId !== "string" ||
      !("revision" in parsed) || !Number.isInteger(parsed.revision))
    throw new Error("The pending prefix split is invalid. Restore browser data before retrying.");
  return parsed as PendingDecision;
}

async function decidePrefixSplit(cardId: string, revision: number, decision: Decision) {
  if (localStorage.getItem("tempo-pending-card-delete-v1"))
    throw new Error("Resolve the pending card deletion before editing a card.");
  let pending = readPending();
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as { state?: string; response?: unknown; error?: { message?: string } };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier prefix split decision failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.cardId === cardId && pending.revision === revision && pending.decision === decision)
        return decision === "accept"
          ? prefixSplitResponseSchema.parse(receipt.response)
          : rejectionSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.cardId !== cardId || pending.revision !== revision || pending.decision !== decision)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), decision, cardId, revision };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  let response = await fetch(`${API_URL}/api/cards/${encodeURIComponent(cardId)}/prefix-split${decision === "reject" ? "/reject" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body: JSON.stringify({ expected_revision: revision }),
  });
  response = await confirmOperationResponse(response);
  const result = decision === "accept"
    ? await readJsonResponse(response, prefixSplitResponseSchema, "accepted prefix split")
    : await readJsonResponse(response, rejectionSchema, "rejected prefix split");
  localStorage.removeItem(PENDING_KEY);
  return result;
}

export async function acceptPrefixSplitCommand(cardId: string, revision: number) {
  return prefixSplitResponseSchema.parse(await decidePrefixSplit(cardId, revision, "accept"));
}

export async function rejectPrefixSplitCommand(cardId: string, revision: number) {
  return rejectionSchema.parse(await decidePrefixSplit(cardId, revision, "reject"));
}
