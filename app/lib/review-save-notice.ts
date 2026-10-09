import { notifications, publishNotification, resolveNotification, updateNotification } from "./notifications";
import { reportDebugError } from "./debug-reporting";
import { PendingOperationError } from "./operation-status";
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
      severity: pending ? "info" as const : (error.blocked || error.classification === "storage") ? "warning" as const : "error" as const,
      message: pending ? pendingReviewMessage : error.classification === "storage" ? "This browser could not update the saved result. Keep its data and open Notifications for details." : error.blocked ? "Saving is blocked. Resolve and retry the operation in Jobs." : "The computer could not save this result. Check Details before retrying.",
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


export function reportReviewSaveFailure(error: unknown, review: Pick<PendingReview, "backendId" | "queueEntryId" | "attemptId">): void {
  try {
    const diagnostic = reportDebugError(error, { source: "training-review-save", operation: "save completed review",
      cardId: review.backendId, queueEntryId: review.queueEntryId, attemptId: review.attemptId, notify: false });
    const key = reviewSaveNoticeKey(review);
    const previous = notifications().find(record => record.key === key && !record.resolvedAt);
    const pending = error instanceof PendingOperationError && !error.blocked;
    const input = { key, source: "training review", active: false, severity: pending ? "info" as const : "error" as const,
      message: pending ? pendingReviewMessage : "This result could not be confirmed. Keep this browser's data and open Notifications for details before retrying.",
      details: { ...previous?.details, cardId: review.backendId, queueEntryId: review.queueEntryId,
        attemptId: review.attemptId ?? "unknown", error: diagnostic.message, errorName: diagnostic.name,
        debugRecordId: diagnostic.id, ...(diagnostic.stack ? { stack: diagnostic.stack } : {}) } };
    if (previous) updateNotification(previous.id, input); else publishNotification(input);
  } catch { /* Diagnostics must never change persistence or retry behavior. */ }
}
