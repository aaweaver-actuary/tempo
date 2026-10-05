import { backgroundFetch } from "./background-fetch";
import { PendingOperationError } from "./operation-status";

// Tag only failures from the fetch boundary or our own aborted deadline.
// A TypeError from validation, storage or application code is not transport.
const transportFailures = new WeakSet<object>();
export function markOpeningEvidenceTimeout(error: unknown, signal: AbortSignal): void {
  if (signal.aborted && error instanceof DOMException && error.name === "AbortError") transportFailures.add(error);
}
export async function openingEvidenceFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = backgroundFetch(input, init);
  try { return await response; }
  catch (error) {
    if (error instanceof TypeError || (error instanceof DOMException && error.name === "AbortError")) transportFailures.add(error);
    throw error;
  }
}
export class OpeningEvidenceHttpError extends Error {
  constructor(readonly status: number) { super(`Could not verify an orphaned opening completion (HTTP ${status}). Its journal remains saved for recovery.`); }
}
export function openingEvidenceRecoveryPolicy(error: unknown): "blocked" | "retry" | "suspend" {
  if (error instanceof PendingOperationError) return error.blocked ? "blocked" : "retry";
  if ((typeof error === "object" && error !== null && transportFailures.has(error)) ||
    (error instanceof OpeningEvidenceHttpError && (error.status === 408 || error.status === 429 || error.status >= 500))) return "retry";
  // Storage, malformed data and unknown errors need repair and an explicit page reload.
  return "suspend";
}
export const openingEvidenceRetryDelays = [1000, 2000, 4000, 8000, 16000, 30000] as const;
