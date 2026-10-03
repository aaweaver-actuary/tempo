import { API_URL } from "../const";
import { confirmOperationResponse } from "./operation-status";
import { publishNotification } from "./notifications";
import { clearTrainingFailureAfterReview } from "./training-failure-outbox";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { openingEvidenceCheckpointSchema } from "../domain/opening-evidence";
import { saveEvidenceAwareReview } from "./opening-evidence-review";
import { acknowledgeOpeningReview, retainOpeningEvidenceForStorageFallback } from "./opening-evidence-journal";

export type PendingReview = {
  backendId: string;
  queueEntryId: number;
  outcome: "again" | "correct";
  guided: boolean;
  attemptId?: string;
  completedAt?: string;
  openingEvidenceCompletion?: OpeningEvidenceCheckpoint;
  evidenceRejected?: string;
  evidenceFallbackReason?: "local_storage_quota";
};

const storageKey = "tempo-pending-training-reviews-v1";
const rejectedOpeningReviewStorageKey = "tempo-rejected-opening-reviews-v1";
const reviewRequestTimeoutMs = 15_000;
let activeFlush: Promise<void> | undefined;

export class ReviewReplayError extends Error {
  constructor(message: string, readonly endpoint: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ReviewReplayError";
  }
}

export function pendingReviews(): PendingReview[] {
  return readStoredReviews(storageKey);
}

function readStoredReviews(key: string): PendingReview[] {
  const stored = localStorage.getItem(key);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== "object" ||
    typeof item.backendId !== "string" ||
    !Number.isInteger(item.queueEntryId) ||
    !["again", "correct"].includes(item.outcome) ||
    typeof item.guided !== "boolean" ||
    (item.attemptId !== undefined && typeof item.attemptId !== "string") ||
    (item.openingEvidenceCompletion !== undefined && !openingEvidenceCheckpointSchema.safeParse(item.openingEvidenceCompletion).success) ||
    (item.evidenceRejected !== undefined && typeof item.evidenceRejected !== "string") ||
    (item.evidenceFallbackReason !== undefined && item.evidenceFallbackReason !== "local_storage_quota") ||
    (item.completedAt !== undefined && typeof item.completedAt !== "string"))) {
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  }
  return parsed as PendingReview[];
}

export function enqueuePendingReview(review: PendingReview): void {
  const pending = pendingReviews();
  if (pending.some((item) => item.queueEntryId === review.queueEntryId)) return;
  const normalizedReview = {
    ...review, attemptId: review.attemptId ?? crypto.randomUUID(),
    completedAt: review.completedAt ?? new Date().toISOString(),
  };
  try {
    localStorage.setItem(storageKey, JSON.stringify([...pending, normalizedReview]));
  } catch (error) {
    if (!(error instanceof DOMException) || error.name !== "QuotaExceededError" || !normalizedReview.openingEvidenceCompletion) throw error;
    const { openingEvidenceCompletion, ...aggregateReview } = normalizedReview;
    // Required: commit the compact envelope and its delivery identity before advancing.
    localStorage.setItem(storageKey, JSON.stringify([...pending, {
      ...aggregateReview, evidenceFallbackReason: "local_storage_quota",
    }]));
    void retainOpeningEvidenceForStorageFallback(openingEvidenceCompletion).catch(error => {
      try {
        publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence-retention:${normalizedReview.attemptId}`,
          message: `The aggregate review is saved locally. Opening evidence could not be retained for diagnosis. Keep this page open to preserve observed work. ${String(error)}` });
      } catch { /* Optional diagnostics cannot undo a durable aggregate review. */ }
    });
  }
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
    const attemptId = review.attemptId ?? `legacy-online:${review.queueEntryId}`;
    const reviewResponse = await saveEvidenceAwareReview({
      endpoint: reviewEndpoint, operationKey: `review-attempt:${attemptId}`, request: requestReviewSave,
      completion: review.openingEvidenceCompletion, evidenceRejected: review.evidenceRejected,
      aggregateOnly: review.evidenceFallbackReason === "local_storage_quota",
      onEvidenceRejected: (message) => {
        const updated = pendingReviews().map((item) =>
          item.attemptId === attemptId ? { ...item, evidenceRejected: message } : item);
        // Required: a reload must retain the aggregate-only delivery identity.
        localStorage.setItem(storageKey, JSON.stringify(updated));
        try {
          const rejected = updated.find((item) => item.attemptId === attemptId);
          const archived = readStoredReviews(rejectedOpeningReviewStorageKey);
          // Best effort: keep the full rejected envelope after the outbox drains,
          // even when the separate IndexedDB journal is unavailable.
          if (rejected && !archived.some((item) => item.attemptId === attemptId))
            localStorage.setItem(rejectedOpeningReviewStorageKey, JSON.stringify([...archived, rejected]));
        } catch {
          try {
            publishNotification({ severity: "warning", source: "opening evidence",
              key: `opening-evidence-archive:${attemptId}`,
              message: "The normal review can still save. An extra diagnostic copy of the rejected opening evidence could not be retained.",
              details: { cardId: review.backendId, queueEntryId: review.queueEntryId } });
          } catch {
            // Diagnostic warnings must not block the required review delivery.
          }
        }
      },
      body: {
        outcome: review.outcome,
        guided: review.guided,
        queue_entry_id: review.queueEntryId,
        attempt_id: attemptId,
        ...(review.completedAt ? { recorded_at: review.completedAt } : {}),
      },
    });
    if (!reviewResponse.ok)
      throw new ReviewReplayError(await responseDetail(reviewResponse), reviewEndpoint);
    const result = await reviewResponse.clone().json() as { persisted?: boolean; warning?: string;
      competing_review?: { outcome?: string | null; completed_at?: string | null } };
    if (!result.persisted)
      throw new ReviewReplayError("The computer did not confirm this review. Retry saving it.", reviewEndpoint);
    const remaining = pendingReviews();
    localStorage.setItem(storageKey, JSON.stringify(
      remaining.filter((item) => item.queueEntryId !== review.queueEntryId),
    ));
    clearTrainingFailureAfterReview(review.queueEntryId);
    if (review.openingEvidenceCompletion)
      void acknowledgeOpeningReview(attemptId).catch(() => undefined);
    if (result.warning) {
      publishNotification({ severity: "warning", source: "training review", key: `review-reconciliation:${attemptId}`,
        message: `${result.warning} Saved result: ${review.outcome} at ${review.completedAt ?? "unknown"}. ` +
          `Other saved result: ${result.competing_review?.outcome ?? "unknown"} at ${result.competing_review?.completed_at ?? "unknown"}.`,
        details: { cardId: review.backendId, queueEntryId: review.queueEntryId,
          outcome: review.outcome, completedAt: review.completedAt ?? "unknown" } });
    }
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
