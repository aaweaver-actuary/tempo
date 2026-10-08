import { notifications, publishNotification, resolveNotification, updateNotification } from "./notifications";
import type { PendingReview, ReviewReplayError } from "./review-outbox";

export const pendingReviewMessage = "Waiting for the computer to confirm this result.";
export function reviewSaveNoticeKey(review: Pick<PendingReview, "attemptId" | "queueEntryId">): string {
  return `review-save:${review.attemptId ?? review.queueEntryId}`;
}
export function reportReviewSaveStatus(error: ReviewReplayError): void {
  try {
    const pending = !error.blocked && ["pending", "transient"].includes(error.classification);
    const key = reviewSaveNoticeKey(error);
    const input = { key, source: "training review", active: false,
      severity: pending ? "info" as const : error.blocked ? "warning" as const : "error" as const,
      message: pending ? pendingReviewMessage : error.blocked ? "Saving is blocked. Resolve and retry the operation in Jobs." : "The computer could not save this result. Check Details before retrying.",
      details: { cardId: error.backendId, queueEntryId: error.queueEntryId, attemptId: error.attemptId,
        error: error.message, classification: error.classification, ...(error.code ? { code: error.code } : {}) } };
    const previous = notifications().find(record => record.key === key && !record.resolvedAt);
    if (previous) updateNotification(previous.id, input); else publishNotification(input);
  } catch { /* Diagnostics must never change persistence or retry behavior. */ }
}
export function confirmReviewSaveNotice(review: PendingReview): void {
  try {
    for (const record of notifications()) if (!record.resolvedAt &&
      (record.key === reviewSaveNoticeKey(review) || (review.attemptId !== undefined && record.details?.attemptId === review.attemptId)))
      resolveNotification(record.id, { severity: "success", message: "Result saved." });
  } catch { /* Confirmation remains authoritative even if notification storage fails. */ }
}
