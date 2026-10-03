import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, PendingOperationError } from "./operation-status";
import { publishNotification, notifications, resolveNotification } from "./notifications";
import { clearTrainingFailureAfterReview } from "./training-failure-outbox";

export type ReviewConflictInformation = { code: string; message: string; retryable: boolean };
export type PendingReview = {
  backendId: string;
  queueEntryId: number;
  outcome: "again" | "correct";
  guided: boolean;
  attemptId?: string;
  completedAt?: string;
  expectedRevision?: number;
  state?: "pending" | "reconciling" | "conflicted";
  reconciliationSequence?: number;
  conflict?: ReviewConflictInformation;
};
export type ReviewFlushResult = { persistedAttemptIds: string[]; conflictedAttemptIds: string[] };

const storageKey = "tempo-pending-training-reviews-v1";
const reviewRequestTimeoutMs = 15_000;
let activeFlush: Promise<ReviewFlushResult> | undefined;

export class ReviewReplayError extends Error {
  readonly backendId: string;
  readonly queueEntryId: number;
  readonly attemptId: string;
  readonly classification: "conflict" | "pending" | "transient";
  constructor(message: string, readonly endpoint: string, review: PendingReview,
    readonly status?: number, readonly code?: string, readonly retryable = true, options?: ErrorOptions) {
    super(message, options);
    this.name = "ReviewReplayError";
    this.backendId = review.backendId;
    this.queueEntryId = review.queueEntryId;
    this.attemptId = logicalAttemptId(review);
    this.classification = options?.cause instanceof PendingOperationError ? "pending"
      : status === 409 && !retryable ? "conflict" : "transient";
  }
}

function logicalAttemptId(review: PendingReview): string {
  return review.attemptId ?? `legacy-online:${review.queueEntryId}`;
}

function savedReviews(): PendingReview[] {
  const stored = localStorage.getItem(storageKey);
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some((item) =>
    !item || typeof item !== "object" ||
    typeof item.backendId !== "string" ||
    !Number.isSafeInteger(item.queueEntryId) || item.queueEntryId <= 0 ||
    !["again", "correct"].includes(item.outcome) ||
    typeof item.guided !== "boolean" ||
    (item.attemptId !== undefined && (typeof item.attemptId !== "string" || !item.attemptId)) ||
    (item.completedAt !== undefined && typeof item.completedAt !== "string") ||
    (item.expectedRevision !== undefined && (!Number.isSafeInteger(item.expectedRevision) || item.expectedRevision < 1)) ||
    (item.reconciliationSequence !== undefined && (!Number.isSafeInteger(item.reconciliationSequence) || item.reconciliationSequence < 1)) ||
    (item.state !== undefined && !["pending", "reconciling", "conflicted"].includes(item.state)) ||
    (item.state === "conflicted" && !validConflict(item.conflict)))) {
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  }
  return parsed as PendingReview[];
}

function validConflict(value: unknown): value is ReviewConflictInformation {
  return !!value && typeof value === "object" && "code" in value && typeof value.code === "string" &&
    "message" in value && typeof value.message === "string" && "retryable" in value && typeof value.retryable === "boolean";
}

export function pendingReviews(): PendingReview[] {
  return savedReviews().filter((review) => review.state !== "conflicted");
}

export function conflictedReviews(): PendingReview[] {
  return savedReviews().filter((review) => review.state === "conflicted");
}

function writeReviews(reviews: PendingReview[]): void {
  localStorage.setItem(storageKey, JSON.stringify(reviews));
  window.dispatchEvent(new Event("tempo:review-outbox"));
}

function replaceReview(review: PendingReview, replacement?: PendingReview): void {
  const identity = logicalAttemptId(review);
  writeReviews(savedReviews().flatMap((item) => logicalAttemptId(item) !== identity ? [item] : replacement ? [replacement] : []));
}

export function enqueuePendingReview(review: PendingReview): void {
  const saved = savedReviews();
  const previous = saved.find((item) => review.attemptId
    ? logicalAttemptId(item) === review.attemptId
    : item.queueEntryId === review.queueEntryId && item.backendId === review.backendId);
  if (previous) {
    if (previous.backendId !== review.backendId || previous.queueEntryId !== review.queueEntryId ||
        previous.outcome !== review.outcome || previous.guided !== review.guided)
      throw new Error("This logical training attempt already has a different completed result.");
    return;
  }
  writeReviews([...saved, { ...review, attemptId: review.attemptId ?? crypto.randomUUID(),
    completedAt: review.completedAt ?? new Date().toISOString() }]);
}

export function discardReviewConflict(attemptId: string): void {
  const review = conflictedReviews().find((item) => logicalAttemptId(item) === attemptId);
  if (!review) throw new Error("This saved conflict is no longer available.");
  replaceReview(review);
  updateReviewConflictNotice();
}

export function retryReviewConflict(attemptId: string): void {
  const review = conflictedReviews().find((item) => logicalAttemptId(item) === attemptId);
  if (!review) throw new Error("This saved conflict is no longer available.");
  replaceReview(review, { ...review, state: "reconciling", conflict: undefined,
    reconciliationSequence: (review.reconciliationSequence ?? 1) + 1 });
  updateReviewConflictNotice();
}

export function updateReviewConflictNotice(): void {
  const conflicts = conflictedReviews();
  if (conflicts.length) publishNotification({ severity: "warning", source: "training review",
    key: "pending-review-conflicts", message: `${conflicts.length} completed training result(s) need review. They remain saved in this browser. Open Review conflicts to retry or export them.`,
    details: { attemptIds: conflicts.map(logicalAttemptId), cardIds: conflicts.map((review) => review.backendId) } });
  else {
    const previous = notifications().find((notice) => notice.key === "pending-review-conflicts" && !notice.resolvedAt);
    if (previous) resolveNotification(previous.id, { severity: "success", message: "Training review conflicts cleared." });
  }
}

async function requestReviewSave(url: string, options: RequestInit, review: PendingReview): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), reviewRequestTimeoutMs);
  try {
    return await confirmOperationResponse(await fetch(url, { ...options, signal: controller.signal }), controller.signal);
  } catch (error) {
    const receiptError = error instanceof FailedOperationError ? error : undefined;
    throw new ReviewReplayError(controller.signal.aborted
      ? "Review save timed out after 15 seconds. Retry save."
      : error instanceof Error ? error.message : String(error), url, review,
      receiptError?.status, receiptError?.code, receiptError?.retryable ?? true, { cause: error });
  } finally { clearTimeout(timeout); }
}

async function responseError(response: Response, endpoint: string, review: PendingReview): Promise<ReviewReplayError> {
  const body = await response.json().catch(() => ({})) as { detail?: unknown; code?: string; retryable?: boolean };
  return new ReviewReplayError(typeof body.detail === "string" ? body.detail : `Local service returned HTTP ${response.status}.`,
    endpoint, review, response.status, body.code, body.retryable ?? response.status >= 500);
}

async function sendReview(review: PendingReview): Promise<Response> {
  const attemptId = logicalAttemptId(review);
  const reconciling = review.state === "reconciling";
  const endpoint = `${API_URL}/api/cards/${encodeURIComponent(review.backendId)}/review${reconciling ? "/reconcile" : ""}`;
  const response = await requestReviewSave(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": reconciling
      ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}` },
    body: JSON.stringify({ outcome: review.outcome, guided: review.guided,
      queue_entry_id: review.queueEntryId, attempt_id: attemptId,
      ...(review.completedAt ? { recorded_at: review.completedAt } : {}),
      ...(review.expectedRevision ? { expected_revision: review.expectedRevision } : {}),
    }),
  }, review);
  if (!response.ok) throw await responseError(response, endpoint, review);
  return response;
}

function retainConflict(review: PendingReview, conflict: ReviewConflictInformation, result: ReviewFlushResult): void {
  // One write changes replay state without any delete/add gap. A storage failure
  // leaves the completed result in the FIFO, so it cannot be silently discarded.
  replaceReview(review, { ...review, state: "conflicted", conflict });
  clearTrainingFailureAfterReview(review.queueEntryId);
  result.conflictedAttemptIds.push(logicalAttemptId(review));
  updateReviewConflictNotice();
}

async function savePendingReviews(): Promise<ReviewFlushResult> {
  const result: ReviewFlushResult = { persistedAttemptIds: [], conflictedAttemptIds: [] };
  updateReviewConflictNotice();
  while (pendingReviews().length) {
    let review = pendingReviews()[0];
    const attemptId = logicalAttemptId(review);
    try {
      if (!review.attemptId) {
        review = { ...review, attemptId };
        replaceReview(pendingReviews()[0], review);
      }
      const records = savedReviews();
      const currentIndex = records.findIndex((item) => logicalAttemptId(item) === attemptId);
      const earlierConflict = records.slice(0, currentIndex).find((item) => item.state === "conflicted" && item.backendId === review.backendId);
      if (earlierConflict) {
        retainConflict(review, { code: "earlier_review_conflict", message: "An earlier result for this card needs review first.", retryable: false }, result);
        continue;
      }
      if (review.guided && review.state !== "reconciling") {
        const failureEndpoint = `${API_URL}/api/queue/entries/${review.queueEntryId}/fail`;
        try {
          const response = await requestReviewSave(failureEndpoint, {
            method: "POST", headers: { "Idempotency-Key": `queue-fail:${review.queueEntryId}` },
          }, review);
          if (!response.ok) throw await responseError(response, failureEndpoint, review);
        } catch (error) {
          // The marker is advisory once a guided result is completed. A stale
          // marker never confirms persistence: the authoritative review below
          // still validates identity and grades the explicit guided=true result.
          if (!(error instanceof ReviewReplayError) || error.status !== 409) throw error;
        }
      }
      let response: Response;
      try { response = await sendReview(review); }
      catch (error) {
        if (!(error instanceof ReviewReplayError) || error.status !== 409 || error.retryable || review.state === "reconciling") throw error;
        review = { ...review, state: "reconciling", reconciliationSequence: 1 };
        replaceReview(review, review);
        response = await sendReview(review);
      }
      const persisted = await response.clone().json() as { persisted?: boolean; conflict?: unknown; warning?: string;
        competing_review?: { outcome?: string | null; completed_at?: string | null } };
      if (review.state === "reconciling" && persisted.persisted === false && validConflict(persisted.conflict)) {
        if (persisted.conflict.retryable)
          throw new ReviewReplayError(persisted.conflict.message, response.url, review, 409, persisted.conflict.code, true);
        retainConflict(review, persisted.conflict, result);
        continue;
      }
      if (persisted.persisted !== true)
        throw new ReviewReplayError("The computer did not confirm this review. Retry saving it.",
          `${API_URL}/api/cards/${review.backendId}/review`, review);
      replaceReview(review);
      clearTrainingFailureAfterReview(review.queueEntryId);
      result.persistedAttemptIds.push(attemptId);
      if (persisted.warning) publishNotification({ severity: "warning", source: "training review", key: `review-reconciliation:${attemptId}`,
        message: `${persisted.warning} Saved result: ${review.outcome} at ${review.completedAt ?? "unknown"}. ` +
          `Other saved result: ${persisted.competing_review?.outcome ?? "unknown"} at ${persisted.competing_review?.completed_at ?? "unknown"}.`,
        details: { cardId: review.backendId, queueEntryId: review.queueEntryId, attemptId,
          outcome: review.outcome, completedAt: review.completedAt ?? "unknown" } });
    } catch (error) {
      if (error instanceof ReviewReplayError) throw error;
      throw new ReviewReplayError(error instanceof Error ? error.message : String(error),
        `${API_URL}/api/cards/${review.backendId}/review`, review, undefined, undefined, true, { cause: error });
    }
  }
  return result;
}

export function flushPendingReviews(): Promise<ReviewFlushResult> {
  if (!activeFlush) activeFlush = savePendingReviews().finally(() => { activeFlush = undefined; });
  return activeFlush;
}
