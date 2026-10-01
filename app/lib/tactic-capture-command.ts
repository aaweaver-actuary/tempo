import { z } from "zod";
import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const captureRequestSchema = z.object({
  capture_id: z.uuid(), starting_fen: z.string().min(1), moves: z.array(z.string()).min(1),
  source_kind: z.enum(["puzzle_rush", "game", "manual", "other"]),
  source_ref: z.string().nullable().optional(), source_url: z.string().nullable().optional(), note: z.string(),
});
export const tacticCaptureResultSchema = z.object({
  capture_id: z.uuid(), card_id: z.string().min(1), reused: z.boolean(), queued: z.literal(true), introduced: z.boolean(),
});
export type TacticCaptureRequest = z.infer<typeof captureRequestSchema>;
export type TacticCaptureDraft = Omit<TacticCaptureRequest, "capture_id">;
const PENDING_KEY = "tempo-pending-tactic-capture-v1";
const pendingSchema = z.object({ body: z.string() });

export function pendingTacticCapture(): TacticCaptureRequest | null {
  const stored = localStorage.getItem(PENDING_KEY);
  if (!stored) return null;
  try { return captureRequestSchema.parse(JSON.parse(pendingSchema.parse(JSON.parse(stored)).body)); }
  catch { throw new Error("The pending capture is unreadable. Restore browser data before capturing another tactic."); }
}

// Store exact transport bytes before the first send. Every unresolved retry uses them.
export async function saveTacticCapture(draft?: TacticCaptureDraft) {
  let pending = pendingTacticCapture();
  let requestBody = pending ? pendingSchema.parse(JSON.parse(localStorage.getItem(PENDING_KEY)!)).body : "";
  if (pending) {
    let receipt: { state?: string; response?: unknown; error?: { message?: string }; last_error?: { message?: string } } = { state: "unknown" };
    try {
      const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.capture_id)}`);
      if (status.ok) receipt = await status.json();
    } catch {
      // The receipt route may be unavailable (including SQLite compatibility).
      // Replaying the exact capture remains safe; editing stays locked.
    }
    if (receipt.state === "complete") {
      const result = tacticCaptureResultSchema.parse(receipt.response);
      if (result.capture_id !== pending.capture_id) throw new Error("The service returned a different capture identity. Retry to resolve this save.");
      localStorage.removeItem(PENDING_KEY);
      return result;
    }
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new FailedOperationError(receipt.error?.message ?? "The capture failed. Edit it and try again.", pending.capture_id);
    }
    if (receipt.state === "blocked") throw new PendingOperationError(pending.capture_id, receipt.last_error?.message, true);
    // A pending or missing receipt can follow a lost send: resend the same ID and bytes.
    if (receipt.state !== "unknown" && receipt.state !== "pending" && receipt.state !== "queued" && receipt.state !== "executing" && receipt.state !== "retrying")
      throw new PendingOperationError(pending.capture_id);
  } else {
    if (!draft) throw new Error("There is no pending capture to resolve.");
    pending = captureRequestSchema.parse({ ...draft, capture_id: crypto.randomUUID() });
    requestBody = JSON.stringify(pending);
    localStorage.setItem(PENDING_KEY, JSON.stringify({ body: requestBody }));
  }
  try {
    let response = await fetch(`${API_URL}/api/tactics/captures`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": pending.capture_id }, body: requestBody,
    });
    // These terminal rejections have no queued business command. 5xx/transport errors stay unresolved.
    if ([400, 409, 422].includes(response.status)) {
      localStorage.removeItem(PENDING_KEY);
      await readJsonResponse(response, tacticCaptureResultSchema, "tactic capture");
    }
    response = await confirmOperationResponse(response);
    const result = await readJsonResponse(response, tacticCaptureResultSchema, "tactic capture");
    if (result.capture_id !== pending.capture_id) throw new Error("The service returned a different capture identity. Retry to resolve this save.");
    localStorage.removeItem(PENDING_KEY);
    return result;
  } catch (error) {
    if (error instanceof FailedOperationError) localStorage.removeItem(PENDING_KEY);
    throw error;
  }
}
