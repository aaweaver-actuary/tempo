import { confirmReviewSaveNotice, reportReviewSaveStatus } from "./review-save-notice";
import { API_URL } from "../const";
import { confirmOperationResponse, FailedOperationError, PendingOperationError } from "./operation-status";
import { publishNotification, notifications, resolveNotification } from "./notifications";
import { clearTrainingFailureAfterReview } from "./training-failure-outbox";

import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { openingEvidenceCheckpointSchema } from "../domain/opening-evidence";
import { saveEvidenceAwareReview } from "./opening-evidence-review";
import { acknowledgeOpeningReview, retainOpeningEvidenceForStorageFallback } from "./opening-evidence-journal";

export type ReviewConflictInformation = { code: string; message: string; retryable: boolean };
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
  expectedRevision?: number;
  automaticRecoverySuppressed?: "failed" | "blocked";
  state?: "pending" | "reconciling" | "conflicted";
  reconciliationSequence?: number;
  conflict?: ReviewConflictInformation;
};
export type ReviewFlushResult = { persistedAttemptIds: string[]; conflictedAttemptIds: string[] };

const rejectedOpeningReviewStorageKey = "tempo-rejected-opening-reviews-v1";
const storageKey = "tempo-pending-training-reviews-v1";
const reviewRequestTimeoutMs = 15_000;
const evidenceStorageErrors = new WeakSet<object>();
const reviewStorageErrors = new WeakSet<object>();
let activeFlush: Promise<ReviewFlushResult> | undefined;
let activeFlushMaximumReviews = Infinity;

export class ReviewReplayError extends Error {
  // Earlier attempts may have settled before a later item failed in this flush.
  flushResult?: ReviewFlushResult;
  readonly blocked: boolean;
  readonly backendId: string;
  readonly queueEntryId: number;
  readonly attemptId: string;
  readonly classification: "conflict" | "pending" | "transient" | "failed" | "storage";
  constructor(message: string, readonly endpoint: string, review: PendingReview,
    readonly status?: number, readonly code?: string, readonly retryable = true, options?: ErrorOptions) {
    super(message, options);
    this.name = "ReviewReplayError";
    this.blocked = options?.cause instanceof PendingOperationError && options.cause.blocked;
    this.backendId = review.backendId;
    this.queueEntryId = review.queueEntryId;
    this.attemptId = logicalAttemptId(review);
    this.classification = options?.cause !== null && typeof options?.cause === "object" && reviewStorageErrors.has(options.cause) ? "storage"
      : options?.cause instanceof PendingOperationError ? "pending"
      : status === 409 && !retryable ? "conflict" : (options?.cause instanceof FailedOperationError && !(status === 409 && options.cause.retryable === true)) ||
        (status !== undefined && status < 500 && status !== 408 && status !== 429 && !(status === 409 && retryable)) ? "failed" : "transient";
  }
}

export function logicalAttemptId(review: Pick<PendingReview, "attemptId" | "queueEntryId">): string {
  return review.attemptId ?? `legacy-online:${review.queueEntryId}`;
}

function savedReviews(): PendingReview[] {
  return readStoredReviews(storageKey);
}

function readStoredReviews(key: string): PendingReview[] {
  const stored = localStorage.getItem(key);
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
    (item.openingEvidenceCompletion !== undefined && !openingEvidenceCheckpointSchema.safeParse(item.openingEvidenceCompletion).success) ||
    (item.evidenceRejected !== undefined && typeof item.evidenceRejected !== "string") ||
    (item.evidenceFallbackReason !== undefined && item.evidenceFallbackReason !== "local_storage_quota") ||
    (item.expectedRevision !== undefined && (!Number.isSafeInteger(item.expectedRevision) || item.expectedRevision < 1)) ||
    (item.reconciliationSequence !== undefined && (!Number.isSafeInteger(item.reconciliationSequence) || item.reconciliationSequence < 1)) ||
    (item.automaticRecoverySuppressed !== undefined && !["failed", "blocked"].includes(item.automaticRecoverySuppressed)) ||
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

// Skip suppressed attempts and their same-card successors, preserving FIFO among
// eligible independent reviews. Conflicts still use the existing reconciliation path.
export function recoverableReviews(explicitRetryAttemptId?: string): PendingReview[] {
  const records = savedReviews();
  return records.filter((review, index) => review.state !== "conflicted" &&
    (!review.automaticRecoverySuppressed || logicalAttemptId(review) === explicitRetryAttemptId) &&
    !records.slice(0, index).some(earlier => earlier.backendId === review.backendId &&
      earlier.state !== "conflicted" && earlier.automaticRecoverySuppressed));
}

export function conflictedReviews(): PendingReview[] {
  return savedReviews().filter((review) => review.state === "conflicted");
}

function writeReviews(reviews: PendingReview[]): void {
  try { localStorage.setItem(storageKey, JSON.stringify(reviews)); }
  catch (error) {
    if (error !== null && typeof error === "object") reviewStorageErrors.add(error);
    throw error;
  }
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
  const normalizedReview = {
    ...review, attemptId: review.attemptId ?? crypto.randomUUID(),
    completedAt: review.completedAt ?? new Date().toISOString(),
  };
  try {
    localStorage.setItem(storageKey, JSON.stringify([...saved, normalizedReview]));
  } catch (error) {
    if (!(error instanceof DOMException) || !["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"].includes(error.name) || !normalizedReview.openingEvidenceCompletion) throw error;
    const { openingEvidenceCompletion, ...aggregateReview } = normalizedReview;
    // Required: commit the compact envelope and its delivery identity before advancing.
    localStorage.setItem(storageKey, JSON.stringify([...saved, {
      ...aggregateReview, evidenceFallbackReason: "local_storage_quota",
    }]));
    void retainOpeningEvidenceForStorageFallback(openingEvidenceCompletion).catch(error => {
      try {
        publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence-retention:${normalizedReview.attemptId}`,
          message: `The aggregate review is saved locally. Opening evidence could not be retained for diagnosis. Existing journal data remains where browser storage succeeded; restore storage before further capture. ${String(error)}` });
      } catch { /* Optional diagnostics cannot undo a durable aggregate review. */ }
    });
  }
  window.dispatchEvent(new Event("tempo:review-outbox"));
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
  replaceReview(review, { ...review, state: "reconciling", conflict: undefined, automaticRecoverySuppressed: undefined,
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

async function requestReviewSave(url: string, options: RequestInit): Promise<Response> {
  return fetch(url, options);
}

async function withReviewSaveDeadline(url: string, review: PendingReview,
  send: (signal: AbortSignal) => Promise<Response>): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), reviewRequestTimeoutMs);
  try {
    return await send(controller.signal);
  } catch (error) {
    const receiptError = error instanceof FailedOperationError ? error : undefined;
    // Required evidence-storage failures must propagate without initiating fallback.
    if (error instanceof ReviewReplayError || (error !== null && typeof error === "object" && evidenceStorageErrors.has(error))) throw error;
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

async function sendReview(review: PendingReview, verifyReceiptFirst = false): Promise<Response> {
  const attemptId = logicalAttemptId(review);
  const reconciling = review.state === "reconciling";
  const endpoint = `${API_URL}/api/cards/${encodeURIComponent(review.backendId)}/review${reconciling ? "/reconcile" : ""}`;
  let failureMarkerSent = false;
  const response = await withReviewSaveDeadline(endpoint, review, signal => saveEvidenceAwareReview({
    endpoint, signal, request: async (url, options) => {
      // Receipt-first recovery reaches this callback only when replay is necessary.
      if (review.guided && !reconciling && !failureMarkerSent) {
        failureMarkerSent = true;
        const failureEndpoint = `${API_URL}/api/queue/entries/${review.queueEntryId}/fail`;
        try {
          const response = await withReviewSaveDeadline(failureEndpoint, review, async signal => confirmOperationResponse(await requestReviewSave(failureEndpoint, {
            method: "POST", headers: { "Idempotency-Key": `queue-fail:${attemptId}`, "Content-Type": "application/json" },
            body: JSON.stringify({ card_id: review.backendId, expected_revision: review.expectedRevision }), signal,
          }), { signal }));
          if (!response.ok) throw await responseError(response, failureEndpoint, review);
        } catch (error) {
          // The marker is advisory once a guided result is completed. A stale
          // marker never confirms persistence: the authoritative review below
          // still validates identity and grades the explicit guided=true result.
          if (!(error instanceof ReviewReplayError) || error.status !== 409) throw error;
        }
      }
      return requestReviewSave(url, options);
    },
    operationKey: reconciling ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}`,
    completion: review.openingEvidenceCompletion, evidenceRejected: review.evidenceRejected,
    verifyReceiptFirst,
    aggregateOnly: review.evidenceFallbackReason === "local_storage_quota",
    onEvidenceRejected: (message) => {
      const updated = savedReviews().map((item) =>
        item.attemptId === attemptId ? { ...item, evidenceRejected: message } : item);
      // Required: a reload must retain the aggregate-only delivery identity.
      try { localStorage.setItem(storageKey, JSON.stringify(updated)); }
      catch (error) {
        if (error !== null && typeof error === "object") evidenceStorageErrors.add(error);
        throw error;
      }
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
    body: { outcome: review.outcome, guided: review.guided,
      queue_entry_id: review.queueEntryId, attempt_id: attemptId,
      ...(review.completedAt ? { recorded_at: review.completedAt } : {}),
      ...(review.expectedRevision ? { expected_revision: review.expectedRevision } : {}),
    },
  }));
  if (!response.ok) throw await responseError(response, endpoint, review);
  return response;
}

function retainConflict(review: PendingReview, conflict: ReviewConflictInformation, result: ReviewFlushResult): void {
  // One write changes replay state without any delete/add gap. A storage failure
  // leaves the completed result in the FIFO, so it cannot be silently discarded.
  const saved = savedReviews().find(item => logicalAttemptId(item) === logicalAttemptId(review)) ?? review;
  replaceReview(review, { ...saved, state: "conflicted", conflict });
  clearTrainingFailureAfterReview(review.queueEntryId, review.backendId, review.expectedRevision);
  result.conflictedAttemptIds.push(logicalAttemptId(review));
  updateReviewConflictNotice();
}

async function savePendingReviews(maximumReviews = Infinity, verifyReceiptFirst = false, explicitRetryAttemptId?: string): Promise<ReviewFlushResult> {
  const result: ReviewFlushResult = { persistedAttemptIds: [], conflictedAttemptIds: [] };
  updateReviewConflictNotice();
  while (recoverableReviews(explicitRetryAttemptId).length && result.persistedAttemptIds.length + result.conflictedAttemptIds.length < maximumReviews) {
    let review = recoverableReviews(explicitRetryAttemptId)[0];
    const attemptId = logicalAttemptId(review);
    try {
      if (!review.attemptId) {
        review = { ...review, attemptId };
        replaceReview(review, review);
      }
      const records = savedReviews();
      const currentIndex = records.findIndex((item) => logicalAttemptId(item) === attemptId);
      const earlierConflict = records.slice(0, currentIndex).find((item) => item.state === "conflicted" && item.backendId === review.backendId);
      if (earlierConflict) {
        retainConflict(review, { code: "earlier_review_conflict", message: "An earlier result for this card needs review first.", retryable: false }, result);
        continue;
      }
      let response: Response;
      try { response = await sendReview(review, verifyReceiptFirst); }
      catch (error) {
        if (!(error instanceof ReviewReplayError) || error.status !== 409 || error.retryable || review.state === "reconciling") throw error;
        review = { ...savedReviews().find(item => logicalAttemptId(item) === attemptId)!, state: "reconciling", reconciliationSequence: 1 };
        replaceReview(review, review);
        response = await sendReview(review, verifyReceiptFirst);
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
      confirmReviewSaveNotice(review);
      if (review.openingEvidenceCompletion)
        void acknowledgeOpeningReview(attemptId).catch(() => undefined);
      clearTrainingFailureAfterReview(review.queueEntryId, review.backendId, review.expectedRevision);
      result.persistedAttemptIds.push(attemptId);
      if (persisted.warning) publishNotification({ severity: "warning", source: "training review", key: `review-reconciliation:${attemptId}`,
        message: `${persisted.warning} Saved result: ${review.outcome} at ${review.completedAt ?? "unknown"}. ` +
          `Other saved result: ${persisted.competing_review?.outcome ?? "unknown"} at ${persisted.competing_review?.completed_at ?? "unknown"}.`,
        details: { cardId: review.backendId, queueEntryId: review.queueEntryId, attemptId,
          outcome: review.outcome, completedAt: review.completedAt ?? "unknown" } });
    } catch (error) {
      if (error instanceof ReviewReplayError) {
        error.flushResult = result;
        const saved = savedReviews().find(item => logicalAttemptId(item) === attemptId);
        const suppression = error.blocked ? "blocked" : ["failed", "conflict"].includes(error.classification) ? "failed" : undefined;
        if (saved && (suppression || (attemptId === explicitRetryAttemptId && saved.automaticRecoverySuppressed && ["pending", "transient"].includes(error.classification)))) {
          // Preserve the latest envelope (including evidence fallback) and original identity.
          // An explicit retry that becomes transient returns to ordinary bounded recovery.
          try { replaceReview(saved, { ...saved, automaticRecoverySuppressed: suppression }); }
          catch (storageError) {
            const replayError = new ReviewReplayError(String(storageError), error.endpoint, saved,
              undefined, undefined, false, { cause: storageError });
            replayError.flushResult = result;
            reportReviewSaveStatus(replayError);
            throw replayError;
          }
        }
        reportReviewSaveStatus(error); throw error;
      }
      if ((error !== null && typeof error === "object" && evidenceStorageErrors.has(error))) throw error;
      const replayError = new ReviewReplayError(error instanceof Error ? error.message : String(error),
        `${API_URL}/api/cards/${review.backendId}/review`, review, undefined, undefined, true, { cause: error });
      replayError.flushResult = result;
      reportReviewSaveStatus(replayError);
      throw replayError;
    }
  }
  return result;
}

// Only the Retry save action supplies an explicit attempt identity. Incidental
// queue refreshes and saving another card must never release terminal suppression.
export function flushPendingReviews(maximumReviews = Infinity, verifyReceiptFirst = false,
  explicitRetryAttemptId?: string): Promise<ReviewFlushResult> {
  if (activeFlush && explicitRetryAttemptId) {
    return activeFlush.catch(() => undefined).then(() => flushPendingReviews(maximumReviews, true, explicitRetryAttemptId));
  }
  if (activeFlush && activeFlushMaximumReviews !== Infinity && maximumReviews === Infinity) return activeFlush.then(async result => {
    if (!recoverableReviews().length) return result;
    const remainder = await flushPendingReviews(maximumReviews, verifyReceiptFirst);
    return { persistedAttemptIds: [...result.persistedAttemptIds, ...remainder.persistedAttemptIds],
      conflictedAttemptIds: [...result.conflictedAttemptIds, ...remainder.conflictedAttemptIds] };
  });
  if (!activeFlush) {
    const retainedRetry = explicitRetryAttemptId && pendingReviews().find(review => logicalAttemptId(review) === explicitRetryAttemptId);
    if (retainedRetry && !recoverableReviews(explicitRetryAttemptId).some(review => logicalAttemptId(review) === explicitRetryAttemptId))
      return Promise.reject(new Error("An earlier result for this card needs attention before this result can retry."));
    activeFlushMaximumReviews = maximumReviews;
    activeFlush = savePendingReviews(maximumReviews, verifyReceiptFirst || !!explicitRetryAttemptId, explicitRetryAttemptId)
      .finally(() => { activeFlush = undefined; });
  }
  return activeFlush;
}
