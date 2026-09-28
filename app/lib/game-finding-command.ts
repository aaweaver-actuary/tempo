import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { parseData } from "./validated-data";

const curationResult = z.object({
  id: z.string(), status: z.enum(["pending", "ignored"]),
  review_after: z.string().optional(),
});
const decisionResult = z.object({
  id: z.string(), status: z.enum(["accepted", "ignored"]),
  scheduling: z.null(), queued: z.boolean(),
});
type FindingAction = { kind: "curation" | "decision"; value: string };
type PendingFindingAction = FindingAction & { operationId: string };

function readPending(storageKey: string): PendingFindingAction | null {
  const saved = localStorage.getItem(storageKey);
  if (!saved) return null;
  const parsed: unknown = JSON.parse(saved);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("kind" in parsed) || !["curation", "decision"].includes(String(parsed.kind)) ||
      !("value" in parsed) || typeof parsed.value !== "string")
    throw new Error("The pending finding decision is invalid. Restore browser data before retrying.");
  return parsed as PendingFindingAction;
}

async function saveFindingAction(findingId: string, action: FindingAction) {
  const storageKey = `tempo-pending-finding-v1:${findingId}`;
  const resultSchema = action.kind === "curation" ? curationResult : decisionResult;
  let pending = readPending(storageKey);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      localStorage.removeItem(storageKey);
      if (pending.kind === action.kind && pending.value === action.value)
        return resultSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "The earlier finding decision failed.");
    } else if (receipt.state === "pending") {
      if (pending.kind !== action.kind || pending.value !== action.value)
        throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { ...action, operationId: crypto.randomUUID() };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }
  const body = action.kind === "curation" ? { action: action.value } : { decision: action.value };
  let response = await fetch(
    `${API_URL}/api/game-findings/${encodeURIComponent(findingId)}/${action.kind}`,
    { method: "POST", headers: {
      "Content-Type": "application/json", "Idempotency-Key": pending.operationId,
    }, body: JSON.stringify(body) },
  );
  response = await confirmOperationResponse(response);
  const raw: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = raw && typeof raw === "object" && "detail" in raw
      ? Reflect.get(raw, "detail") : null;
    throw new Error(typeof detail === "string" ? detail :
      `Could not save the finding decision (HTTP ${response.status}).`);
  }
  const result = action.kind === "curation"
    ? parseData(curationResult, raw, "finding curation")
    : parseData(decisionResult, raw, "finding decision");
  if (result.id !== findingId) throw new Error("Finding decision response did not match the request.");
  localStorage.removeItem(storageKey);
  return result;
}

export async function curateGameFinding(findingId: string, action: "skip" | "ignore") {
  return saveFindingAction(findingId, { kind: "curation", value: action });
}

export async function decideGameFinding(findingId: string, decision: "accepted" | "ignored") {
  return saveFindingAction(findingId, { kind: "decision", value: decision });
}
