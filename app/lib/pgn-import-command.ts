import { API_URL } from "../const";
import { importResultSchema } from "../domain/schemas";
import { confirmOperationResponse, PendingOperationError } from "./operation-status";
import { readJsonResponse } from "./validated-data";

const PENDING_KEY = "tempo-pending-pgn-import-v1";
type PendingImport = { operationId: string; fingerprint: string };

export async function savePgnImportCommand(
  file: File, trainedColor: "white" | "black", initialDepth: number,
) {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const fingerprint = [file.name, trainedColor, initialDepth,
    Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")].join(":");
  const stored = localStorage.getItem(PENDING_KEY);
  let pending: PendingImport | null = stored ? JSON.parse(stored) as PendingImport : null;
  if (pending) {
    if (!pending.operationId || !pending.fingerprint)
      throw new Error("The pending PGN import is invalid. Restore browser data before retrying.");
    const status = await fetch(`${API_URL}/api/operations/${encodeURIComponent(pending.operationId)}`);
    if (!status.ok) throw new PendingOperationError(pending.operationId);
    const receipt = await status.json() as {
      state?: string; response?: unknown; error?: { message?: string };
    };
    if (receipt.state === "failed") {
      localStorage.removeItem(PENDING_KEY);
      throw new Error(receipt.error?.message ?? "The earlier PGN import failed.");
    }
    if (receipt.state === "complete") {
      localStorage.removeItem(PENDING_KEY);
      if (pending.fingerprint === fingerprint) return importResultSchema.parse(receipt.response);
      pending = null;
    } else if (receipt.state === "pending") {
      if (pending.fingerprint !== fingerprint) throw new PendingOperationError(pending.operationId);
    } else throw new PendingOperationError(pending.operationId);
  }
  if (!pending) {
    pending = { operationId: crypto.randomUUID(), fingerprint };
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }
  const form = new FormData();
  form.append("file", file);
  form.append("trained_color", trainedColor);
  form.append("initial_depth", String(initialDepth));
  let response = await fetch(`${API_URL}/api/imports/pgn`, {
    method: "POST", headers: { "Idempotency-Key": pending.operationId }, body: form,
  });
  response = await confirmOperationResponse(response);
  if (!response.ok) throw new Error("Could not import this PGN file.");
  const result = await readJsonResponse(response, importResultSchema, "PGN import");
  localStorage.removeItem(PENDING_KEY);
  return result;
}
