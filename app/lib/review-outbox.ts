import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";

export type PendingReview = {
  backendId: string;
  queueEntryId: number;
  outcome: "again" | "correct";
  guided: boolean;
};

const storageKey = "tempo-pending-training-reviews-v1";
const reviewRequestTimeoutMs = 15_000;
let activeFlush: Promise<void> | undefined;

export class ReviewReplayError extends Error {
  constructor(message: string, readonly endpoint: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ReviewReplayError";
  }
}

export function pendingReviews(): PendingReview[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== "object" ||
    typeof item.backendId !== "string" ||
    !Number.isInteger(item.queueEntryId) ||
    !["again", "correct"].includes(item.outcome) ||
    typeof item.guided !== "boolean")) {
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  }
  return parsed as PendingReview[];
}

export function enqueuePendingReview(review: PendingReview): void {
  const pending = pendingReviews();
  if (pending.some((item) => item.queueEntryId === review.queueEntryId)) return;
  localStorage.setItem(storageKey, JSON.stringify([...pending, review]));
}

async function requestReviewSave(url: string, options: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), reviewRequestTimeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted)
      throw new ReviewReplayError("Review save timed out after 15 seconds. Retry save.", url, { cause: error });
    throw new ReviewReplayError(error instanceof Error ? error.message : String(error), url, { cause: error });
  } finally {
    clearTimeout(timeout);
  }
}

async function savePendingReviews(): Promise<void> {
  while (pendingReviews().length) {
    const review = pendingReviews()[0];
    if (review.guided) {
      const failureEndpoint = `${API_URL}/api/queue/entries/${review.queueEntryId}/fail`;
      const failureResponse = await confirmOperationResponse(await requestReviewSave(failureEndpoint, {
        method: "POST", headers: { "Idempotency-Key": `queue-fail:${review.queueEntryId}` },
      }));
      if (!failureResponse.ok && failureResponse.status !== 409)
        throw new ReviewReplayError(await responseDetail(failureResponse), failureEndpoint);
    }
    const reviewEndpoint = `${API_URL}/api/cards/${review.backendId}/review`;
    const reviewResponse = await confirmOperationResponse(await requestReviewSave(reviewEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": `review:${review.queueEntryId}` },
      body: JSON.stringify({
        outcome: review.outcome,
        guided: review.guided,
        queue_entry_id: review.queueEntryId,
      }),
    }));
    if (!reviewResponse.ok)
      throw new ReviewReplayError(await responseDetail(reviewResponse), reviewEndpoint);
    const remaining = pendingReviews();
    localStorage.setItem(storageKey, JSON.stringify(
      remaining.filter((item) => item.queueEntryId !== review.queueEntryId),
    ));
  }
}

async function responseDetail(response: Response): Promise<string> {
  const body = await response.json().catch(() => ({})) as { detail?: string };
  return body.detail ?? `Local service returned HTTP ${response.status}.`;
}

export function flushPendingReviews(): Promise<void> {
  if (!activeFlush) {
    activeFlush = savePendingReviews().finally(() => { activeFlush = undefined; });
  }
  return activeFlush;
}
