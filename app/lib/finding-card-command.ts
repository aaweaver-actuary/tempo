import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { parseData } from "./validated-data";

const cardResult = z.object({
  preview: z.object({
    starting_fen: z.string(), moves: z.array(z.string()), best_move: z.string(),
    trained_color: z.string(), existing_card_id: z.string().nullable().optional(),
  }),
  saved: z.boolean(), card_id: z.string().optional(), reused: z.boolean().optional(),
});
type FindingCardRequest = {
  save: boolean;
  starting_fen?: string;
  moves?: string[];
  trained_color?: "white" | "black";
};
type PendingCard = { operationId: string; body: string };

function readPending(storageKey: string): PendingCard | null {
  const saved = localStorage.getItem(storageKey);
  if (!saved) return null;
  const parsed: unknown = JSON.parse(saved);
  if (typeof parsed !== "object" || parsed === null ||
      !("operationId" in parsed) || typeof parsed.operationId !== "string" ||
      !("body" in parsed) || typeof parsed.body !== "string")
    throw new Error("The pending finding card is invalid. Restore browser data before retrying.");
  return parsed as PendingCard;
}

async function readCardResponse(response: Response) {
  const raw: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = raw && typeof raw === "object" && "detail" in raw
      ? Reflect.get(raw, "detail") : null;
    throw new Error(typeof detail === "string" ? detail :
      `Could not prepare this finding as a study card (HTTP ${response.status}).`);
  }
  return parseData(cardResult, raw, "finding card");
}

export async function prepareFindingCard(findingId: string, request: FindingCardRequest) {
  const body = JSON.stringify(request);
  const url = `${API_URL}/api/game-findings/${encodeURIComponent(findingId)}/card`;
  if (!request.save) {
    return readCardResponse(await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" }, body,
    }));
  }
  const storageKey = `tempo-pending-finding-card-v1:${findingId}`;
  let pending = readPending(storageKey);
  if (pending) {
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "complete") {
      const result = cardResult.parse(receipt.response);
      localStorage.removeItem(storageKey);
      if (pending.body === body) return result;
      pending = null;
    } else if (receipt.state === "failed") {
      localStorage.removeItem(storageKey);
      throw new Error(receipt.error?.message ?? "The earlier finding card save failed.");
    } else if (receipt.state === "pending") {
      if (pending.body !== body) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), body };
    localStorage.setItem(storageKey, JSON.stringify(pending));
  }
  let response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": pending.operationId },
    body,
  });
  response = await confirmOperationResponse(response);
  const result = await readCardResponse(response);
  if (!result.saved || !result.card_id)
    throw new Error("The study card save has no confirmed card ID.");
  localStorage.removeItem(storageKey);
  return result;
}
