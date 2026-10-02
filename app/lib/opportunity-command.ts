import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_PREFIX = "tempo-pending-opportunity-v1:";
type Action = "dismiss" | "acknowledge" | "snooze" | "train";
type PendingAction = { operationId: string; repertoireId: string; action: Action; evidenceFingerprint?: string };
const responseSchemas = {
  dismiss: z.strictObject({ dismissed: z.literal(true) }),
  acknowledge: z.strictObject({ acknowledged: z.literal(true) }),
  snooze: z.strictObject({ snoozed: z.literal(true) }),
  train: z.strictObject({ card_id: z.string(), queued: z.literal(true), idempotent: z.boolean() }),
};

function readPending(key: string): PendingAction | null {
  const stored = localStorage.getItem(key);
  if (!stored) return null;
  const parsed: unknown = JSON.parse(stored);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("repertoireId" in parsed) || typeof parsed.repertoireId !== "string" ||
      !("action" in parsed) || !["dismiss", "acknowledge", "snooze", "train"].includes(String(parsed.action)))
    throw new Error("The pending discovery action is invalid. Restore browser data before retrying.");
  if ("evidenceFingerprint" in parsed && typeof parsed.evidenceFingerprint !== "string")
    throw new Error("The pending discovery evidence revision is invalid.");
  return parsed as PendingAction;
}

function validateResult(action: Action, value: unknown): void {
  responseSchemas[action].parse(value);
}

export async function applyOpportunityCommand(
  repertoireId: string, opportunityId: string, action: Action, evidenceFingerprint?: string,
): Promise<void> {
  const key = `${PENDING_PREFIX}${opportunityId}`;
  let pending = readPending(key);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(key);
      throw new Error(receipt.error?.message ?? "The earlier discovery action failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(key);
      if (pending.repertoireId === repertoireId && pending.action === action &&
          pending.evidenceFingerprint === evidenceFingerprint) {
        validateResult(action, receipt.response);
        return;
      }
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.repertoireId !== repertoireId || pending.action !== action ||
          pending.evidenceFingerprint !== evidenceFingerprint)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), repertoireId, action,
      ...(evidenceFingerprint ? { evidenceFingerprint } : {}) };
    localStorage.setItem(key, JSON.stringify(pending));
  }
  let response = await fetch(
    `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/opportunities/${encodeURIComponent(opportunityId)}/${action}`,
    { method: "POST", headers: { "Idempotency-Key": pending.operationId,
        ...(pending.evidenceFingerprint ? { "Content-Type": "application/json" } : {}) },
      ...(pending.evidenceFingerprint ? { body: JSON.stringify({ evidence_fingerprint: pending.evidenceFingerprint }) } : {}) },
  );
  response = await confirmOperationResponse(response);
  const result = await readJsonResponse(response, z.unknown(), `${action} discovery`);
  validateResult(action, result);
  localStorage.removeItem(key);
}
