import { readRejectedOpeningReviews, writeRejectedOpeningReviews } from "../../database/browser/adapters/practice/review-archive";
import { LEARNING_URL as API_URL, learningFetch as fetch } from "./study-connection";
import type { StudySession } from "@tempo/study-session";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import type { IntentRecord } from "@tempo/browser-intents";
import { studyIntentRecords, studyIntentStore, updateStudyIntent, refreshStudyIntents, immutableReviewPayload } from "../../database/browser/study-intent-storage";
import { confirmReviewSaveNotice, reportReviewSaveStatus } from "./review-save-notice";
import type { PreparedNextIdentity, PreparedContinuation } from "./prepared-review-continuation";
import { confirmOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { publishNotification, notifications, resolveNotification } from "./notifications";
import { clearTrainingFailureAfterReview } from "./training-failure-outbox";

import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { validLegacyReview, validConflict } from "./study-legacy-validation";
import { saveEvidenceAwareReview } from "./opening-evidence-review";
import { acknowledgeOpeningReview, retainOpeningEvidenceForStorageFallback } from "./opening-evidence-journal";
import { IntentStorageError } from "@tempo/browser-intents";

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
  preparedNext?: PreparedNextIdentity;
  automaticRecoverySuppressed?: "failed" | "blocked";
  deliveryStarted?: boolean;
  state?: "pending" | "reconciling" | "conflicted";
  reconciliationSequence?: number;
  conflict?: ReviewConflictInformation;
};
export type ReviewFlushResult = { persistedAttemptIds: string[]; conflictedAttemptIds: string[];
  preparedContinuations?: Record<string, PreparedContinuation> };

const reviewRequestTimeoutMs = 15_000;
const evidenceStorageErrors = new WeakSet<object>();
const reviewStorageErrors = new WeakSet<object>();
let activeFlush: Promise<ReviewFlushResult> | undefined;
let activeFlushMaximumReviews = Infinity;
const flushProgressListeners = new Set<(result: ReviewFlushResult) => void>();
let currentFlushProgress: ReviewFlushResult | undefined;

function attemptSettled(result: ReviewFlushResult, attemptId: string): boolean {
  return result.persistedAttemptIds.includes(attemptId) || result.conflictedAttemptIds.includes(attemptId);
}

function publishFlushProgress(result: ReviewFlushResult): void {
  currentFlushProgress = result;
  for (const listener of flushProgressListeners) listener(result);
}

function waitForSubmittedAttempt(flush: Promise<ReviewFlushResult>, attemptId: string): Promise<ReviewFlushResult> {
  return new Promise((resolve, reject) => {
    const observe = (result: ReviewFlushResult) => {
      if (!attemptSettled(result, attemptId)) return;
      flushProgressListeners.delete(observe);
      resolve({ persistedAttemptIds: [...result.persistedAttemptIds], conflictedAttemptIds: [...result.conflictedAttemptIds],
        preparedContinuations: { ...result.preparedContinuations } });
    };
    flushProgressListeners.add(observe);
    if (currentFlushProgress) observe(currentFlushProgress);
    void flush.then(result => { flushProgressListeners.delete(observe); resolve(result); },
      error => { flushProgressListeners.delete(observe); reject(error); });
  });
}

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
    this.classification = options?.cause instanceof IntentStorageError || (options?.cause !== null && typeof options?.cause === "object" && reviewStorageErrors.has(options.cause)) ? "storage"
      : options?.cause instanceof PendingOperationError ? "pending"
      : status === 409 && !retryable ? "conflict" : (options?.cause instanceof FailedOperationError && !(status === 409 && options.cause.retryable === true)) ||
        (status !== undefined && status < 500 && status !== 408 && status !== 429 && !(status === 409 && retryable)) ? "failed" : "transient";
  }
}

export function logicalAttemptId(review: Pick<PendingReview, "attemptId" | "queueEntryId">): string {
  return review.attemptId ?? `legacy-online:${review.queueEntryId}`;
}

function savedReviews(): PendingReview[] {
  return studyIntentRecords("review").filter(record => record.state === "pending" || record.state === "conflicted")
    .map(record => {
      const review = { ...(record.data ?? record.payload) as PendingReview };
      delete review.deliveryStarted;
      return { ...review, ...(record.state === "conflicted" ? { state: "conflicted" as const } : {}) };
    });
}

function readStoredReviews(): PendingReview[] {
  const stored = readRejectedOpeningReviews();
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some(item => !validLegacyReview(item)))
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  return parsed as PendingReview[];
}

export function pendingReviews(): PendingReview[] {
  return savedReviews().filter((review) => review.state !== "conflicted");
}

// Skip suppressed attempts and their same-card successors, preserving FIFO among
// eligible independent reviews. Conflicts still use the existing reconciliation path.
export function recoverableReviews(explicitRetryAttemptId?: string, deferredAttemptIds: ReadonlySet<string> = new Set()): PendingReview[] {
  const records = savedReviews();
  return records.filter((review, index) => review.state !== "conflicted" &&
    !deferredAttemptIds.has(logicalAttemptId(review)) &&
    (!review.automaticRecoverySuppressed || logicalAttemptId(review) === explicitRetryAttemptId) &&
    !records.slice(0, index).some(earlier => earlier.backendId === review.backendId &&
      earlier.state !== "conflicted" && (earlier.automaticRecoverySuppressed || deferredAttemptIds.has(logicalAttemptId(earlier)))));
}

export function conflictedReviews(): PendingReview[] {
  return savedReviews().filter((review) => review.state === "conflicted");
}

async function replaceReview(review: PendingReview, replacement?: PendingReview): Promise<void> {
  await updateStudyIntent(`review:${logicalAttemptId(review)}`, current => ({
    state: replacement?.state === "conflicted" ? "conflicted" : replacement ? "pending" : "committed",
    data: replacement ? { ...(current.data as PendingReview), ...replacement } : current.data, receipt: replacement ? current.receipt : { persisted: true },
  }));
}

export async function enqueuePendingReview(review: PendingReview): Promise<string> {
  const store = await studyIntentStore();
  const retained = review.attemptId ? await store.get(`review:${review.attemptId}`) : undefined;
  const previous = retained ? (retained.data ?? retained.payload) as PendingReview : savedReviews().find(item => review.attemptId
    ? logicalAttemptId(item) === review.attemptId
    : item.queueEntryId === review.queueEntryId && item.backendId === review.backendId);
  if (previous) {
    for (const field of ["backendId", "queueEntryId", "outcome", "guided", "completedAt", "expectedRevision", "openingEvidenceCompletion", "preparedNext"] as const)
      if (!(field === "openingEvidenceCompletion" && previous.evidenceFallbackReason) && review[field] !== undefined && JSON.stringify(previous[field]) !== JSON.stringify(review[field]))
        throw new Error("This logical training attempt already has a different completed result.");
    return logicalAttemptId(previous);
  }
  const normalizedReview = { ...review, attemptId: review.attemptId ?? crypto.randomUUID(), completedAt: review.completedAt ?? new Date().toISOString() };
  const retain = async (payload: PendingReview) => {
    const coordinator = await reviewStudySession();
    await coordinator.submit({ key: `review:${normalizedReview.attemptId}`, kind: "review", attemptId: normalizedReview.attemptId,
    requestId: `review-attempt:${normalizedReview.attemptId}`, endpoint: `/api/cards/${encodeURIComponent(review.backendId)}/review`,
    revision: review.expectedRevision, createdAt: normalizedReview.completedAt, payload: immutableReviewPayload(payload) }, payload, { deliver: false });
    await refreshStudyIntents();
  };
  try { await retain(normalizedReview); }
  catch (error) {
    const cause = error instanceof IntentStorageError ? error.cause : error;
    if (!(cause instanceof DOMException) || !["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"].includes(cause.name) || !normalizedReview.openingEvidenceCompletion) throw error;
    const { openingEvidenceCompletion, ...aggregate } = normalizedReview;
    await retain({ ...aggregate, evidenceFallbackReason: "local_storage_quota" });
    void retainOpeningEvidenceForStorageFallback(openingEvidenceCompletion).catch(() => {
      publishNotification({ severity: "warning", source: "opening evidence", key: `opening-evidence-retention:${normalizedReview.attemptId}`,
        message: "The aggregate review is saved locally. Its opening evidence could not be retained; restore browser storage before further capture." });
    });
  }
  return normalizedReview.attemptId;
}

export async function discardReviewConflict(attemptId: string): Promise<void> {
  const review = conflictedReviews().find((item) => logicalAttemptId(item) === attemptId);
  if (!review) throw new Error("This saved conflict is no longer available.");
  await updateStudyIntent(`review:${attemptId}`, current => ({ ...current, state: "discarded" }));
  updateReviewConflictNotice();
}

export async function retryReviewConflict(attemptId: string): Promise<void> {
  const review = conflictedReviews().find((item) => logicalAttemptId(item) === attemptId);
  if (!review) throw new Error("This saved conflict is no longer available.");
  await replaceReview(review, { ...review, state: "reconciling", conflict: undefined, automaticRecoverySuppressed: undefined,
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
  send: (signal: AbortSignal) => Promise<Response>, parentSignal?: AbortSignal): Promise<Response> {
  const controller = new AbortController();
  const abort = () => controller.abort(parentSignal?.reason);
  parentSignal?.addEventListener("abort", abort, { once: true });
  if (parentSignal?.aborted) abort();
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
  } finally { clearTimeout(timeout); parentSignal?.removeEventListener("abort", abort); }
}

async function responseError(response: Response, endpoint: string, review: PendingReview): Promise<ReviewReplayError> {
  const body = await response.json().catch(() => ({})) as { detail?: unknown; code?: string; retryable?: boolean };
  return new ReviewReplayError(typeof body.detail === "string" ? body.detail : `Local service returned HTTP ${response.status}.`,
    endpoint, review, response.status, body.code, body.retryable ?? response.status >= 500);
}

async function sendReview(review: PendingReview, verifyReceiptFirst = false, parentSignal?: AbortSignal): Promise<Response> {
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
          }), { signal }), parentSignal);
          if (!response.ok) throw await responseError(response, failureEndpoint, review);
        } catch (error) {
          // The marker is advisory once a guided result is completed. A stale
          // marker never confirms persistence: the authoritative review below
          // still validates identity and grades the explicit guided=true result.
          if (!(error instanceof ReviewReplayError) || error.status !== 409) throw error;
        }
      }
      // Reconciliation and evidence fallback use separately retained protocol commands.
      // The parent review keeps its original answer, timestamp and evidence envelope.
      const operationId = String((options.headers as Record<string, string>)["Idempotency-Key"]);
      const deliveryKey = `review-delivery:${operationId}`;
      const store = await studyIntentStore();
      const existing = await store.get(deliveryKey);
      await store.put({ key: deliveryKey, kind: "review-delivery", attemptId, requestId: operationId,
        endpoint: url, revision: review.expectedRevision, createdAt: existing?.createdAt ?? review.completedAt ?? "legacy-time-unavailable",
        payload: { parentKey: `review:${attemptId}`, headers: options.headers, body: JSON.parse(String(options.body)) } });
      const response = await requestReviewSave(url, options);
      const receipt = await response.clone().json().catch(() => undefined) as { persisted?: boolean } | undefined;
      if (receipt?.persisted === true) await store.update(deliveryKey, current => ({ ...current, state: "committed", receipt }));
      return response;
    },
    operationKey: reconciling ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}`,
    completion: review.openingEvidenceCompletion, evidenceRejected: review.evidenceRejected,
    verifyReceiptFirst,
    aggregateOnly: review.evidenceFallbackReason === "local_storage_quota",
    onEvidenceRejected: async (message) => {
      const updated = savedReviews().map((item) =>
        item.attemptId === attemptId ? { ...item, evidenceRejected: message } : item);
      // Required: a reload must retain the aggregate-only delivery identity.
      try {
        const latest = updated.find(item => item.attemptId === attemptId);
        if (latest) await replaceReview(latest, latest);
      }
      catch (error) {
        if (error !== null && typeof error === "object") evidenceStorageErrors.add(error);
        throw error;
      }
      try {
        const rejected = updated.find((item) => item.attemptId === attemptId);
        const archived = readStoredReviews();
        // Best effort: keep the full rejected envelope after the outbox drains,
        // even when the separate IndexedDB journal is unavailable.
        if (rejected && !archived.some((item) => item.attemptId === attemptId))
          writeRejectedOpeningReviews(archived, rejected);
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
      ...(review.preparedNext ? { prepared_next: review.preparedNext } : {}),
    },
  }), parentSignal);
  if (!response.ok) throw await responseError(response, endpoint, review);
  return response;
}

type LegacyReviewReceipt = { persisted?: boolean; conflict?: unknown; warning?: string; prepared_continuation?: PreparedContinuation;
  competing_review?: { outcome?: string | null; completed_at?: string | null } };

async function deliverLegacyReview(record: IntentRecord, signal: AbortSignal) {
  let review = (record.data ?? record.payload) as PendingReview;
  const attemptId = logicalAttemptId(review);
  const records = savedReviews();
  const currentIndex = records.findIndex(item => logicalAttemptId(item) === attemptId);
  if (records.slice(0, currentIndex).some(item => item.state === "conflicted" && item.backendId === review.backendId))
    return { state: "conflicted" as const, data: { ...review, state: "conflicted", conflict: {
      code: "earlier_review_conflict", message: "An earlier result for this card needs review first.", retryable: false } } };
  const verifyReceiptFirst = review.deliveryStarted === true;
  await replaceReview(review, { ...review, deliveryStarted: true });
  let response: Response;
  try { response = await sendReview(review, verifyReceiptFirst, signal); }
  catch (error) {
    if (!(error instanceof ReviewReplayError) || error.status !== 409 || error.retryable || review.state === "reconciling") throw error;
    review = { ...savedReviews().find(item => logicalAttemptId(item) === attemptId)!, state: "reconciling", reconciliationSequence: 1 };
    await replaceReview(review, review);
    response = await sendReview(review, false, signal);
  }
  const persisted = await response.clone().json() as LegacyReviewReceipt;
  if (review.state === "reconciling" && persisted.persisted === false && validConflict(persisted.conflict)) {
    if (persisted.conflict.retryable) throw new ReviewReplayError(persisted.conflict.message, response.url, review, 409, persisted.conflict.code, true);
    return { state: "conflicted" as const, receipt: persisted, data: { ...review, state: "conflicted", conflict: persisted.conflict } };
  }
  if (persisted.persisted !== true) throw new ReviewReplayError("The computer did not confirm this review. Retry saving it.",
    `${API_URL}/api/cards/${review.backendId}/review`, review);
  return { state: "committed" as const, receipt: persisted };
}

async function checkLegacyReviewReceipt(record: IntentRecord, signal: AbortSignal) {
  const review = (record.data ?? record.payload) as PendingReview;
  const attemptId = logicalAttemptId(review);
  const operationId = review.state === "reconciling" ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}`;
  const response = await readOperationResponse(`${operationId}${review.evidenceRejected || review.evidenceFallbackReason ? ":aggregate-only" : ""}`, { allowMissing: true, signal });
  if (response.status === 404) return undefined;
  const receipt = await response.json() as LegacyReviewReceipt;
  if (receipt.persisted === true) return { state: "committed" as const, receipt };
  if (receipt.persisted === false && validConflict(receipt.conflict) && !receipt.conflict.retryable)
    return { state: "conflicted" as const, receipt, data: { ...review, state: "conflicted", conflict: receipt.conflict } };
  return { state: "pending" as const };
}
registerStudyDeliveryAdapter("review", deliverLegacyReview, checkLegacyReviewReceipt);
export async function reviewStudySession(): Promise<StudySession> { return studySession(); }

async function savePendingReviews(maximumReviews = Infinity, verifyReceiptFirst = false, explicitRetryAttemptId?: string,
  deferredAttemptIds?: ReadonlySet<string>, stopAfterAttemptId?: string): Promise<ReviewFlushResult> {
  await studyIntentStore();
  const result: ReviewFlushResult = { persistedAttemptIds: [], conflictedAttemptIds: [] };
  if (stopAfterAttemptId) {
    const retained = await (await studyIntentStore()).get(`review:${stopAfterAttemptId}`);
    if (retained?.state === "committed" && (retained.receipt as LegacyReviewReceipt | undefined)?.persisted === true) {
      result.persistedAttemptIds.push(stopAfterAttemptId);
      const review = retained.payload as PendingReview;
      const continuation = (retained.receipt as LegacyReviewReceipt).prepared_continuation;
      if (continuation && review.preparedNext && Object.entries(review.preparedNext).every(([key, value]) => Reflect.get(continuation, key) === value))
        result.preparedContinuations = { [stopAfterAttemptId]: continuation };
      return result;
    }
  }
  const reviewsForSubmission = () => {
    const availableReviews = recoverableReviews(explicitRetryAttemptId, deferredAttemptIds);
    if (!stopAfterAttemptId) return availableReviews;
    const originalReviews = savedReviews();
    const submittedIndex = originalReviews.findIndex(review => logicalAttemptId(review) === stopAfterAttemptId);
    if (submittedIndex < 0) return [];
    const submittedReview = originalReviews[submittedIndex];
    const requiredAttemptIds = new Set(originalReviews.slice(0, submittedIndex + 1)
      .filter(review => review.backendId === submittedReview.backendId).map(logicalAttemptId));
    return availableReviews.filter(review => requiredAttemptIds.has(logicalAttemptId(review)));
  };
  updateReviewConflictNotice();
  while (reviewsForSubmission().length && result.persistedAttemptIds.length + result.conflictedAttemptIds.length < maximumReviews &&
      !(stopAfterAttemptId && attemptSettled(result, stopAfterAttemptId))) {
    const review = reviewsForSubmission()[0];
    const attemptId = logicalAttemptId(review);
    try {
      if (verifyReceiptFirst && !review.deliveryStarted) await replaceReview(review, { ...review, deliveryStarted: true });
      const settlement = await (await reviewStudySession()).checkSave(`review:${attemptId}`);
      await refreshStudyIntents();
      if (settlement.state === "pending") break;
      if (settlement.state === "conflicted") {
        await clearTrainingFailureAfterReview(review.queueEntryId, review.backendId, review.expectedRevision);
        result.conflictedAttemptIds.push(attemptId); updateReviewConflictNotice(); publishFlushProgress(result); continue;
      }
      if (settlement.state === "discarded") continue;
      const persisted = settlement.receipt as LegacyReviewReceipt | undefined;
      if (settlement.state !== "committed" || persisted?.persisted !== true)
        throw new Error("The service did not confirm a persisted review. Retry its original receipt.");
      confirmReviewSaveNotice(review);
      if (review.openingEvidenceCompletion)
        void acknowledgeOpeningReview(attemptId).catch(() => undefined);
      await clearTrainingFailureAfterReview(review.queueEntryId, review.backendId, review.expectedRevision);
      result.persistedAttemptIds.push(attemptId);
      performance.mark("tempo.review.confirmed", { detail: { attemptId } });
      if (persisted.prepared_continuation && typeof persisted.prepared_continuation === "object" && review.preparedNext &&
          (persisted.prepared_continuation.attempt_failed === undefined || typeof persisted.prepared_continuation.attempt_failed === "boolean") &&
          Object.entries(review.preparedNext).every(([key, value]) => Reflect.get(persisted.prepared_continuation!, key) === value))
        (result.preparedContinuations ??= {})[attemptId] = persisted.prepared_continuation;
      if (persisted.warning) publishNotification({ severity: "warning", source: "training review", key: `review-reconciliation:${attemptId}`,
        message: `${persisted.warning} Saved result: ${review.outcome} at ${review.completedAt ?? "unknown"}. ` +
          `Other saved result: ${persisted.competing_review?.outcome ?? "unknown"} at ${persisted.competing_review?.completed_at ?? "unknown"}.`,
        details: { cardId: review.backendId, queueEntryId: review.queueEntryId, attemptId,
          outcome: review.outcome, completedAt: review.completedAt ?? "unknown" } });
      publishFlushProgress(result);
    } catch (error) {
      if (error instanceof ReviewReplayError) {
        error.flushResult = result;
        const saved = savedReviews().find(item => logicalAttemptId(item) === attemptId);
        const suppression = error.blocked ? "blocked" : ["failed", "conflict"].includes(error.classification) ? "failed" : undefined;
        if (saved && (suppression || (attemptId === explicitRetryAttemptId && saved.automaticRecoverySuppressed && ["pending", "transient"].includes(error.classification)))) {
          // Preserve the latest envelope (including evidence fallback) and original identity.
          // An explicit retry that becomes transient returns to ordinary bounded recovery.
          try { await replaceReview(saved, { ...saved, automaticRecoverySuppressed: suppression }); }
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

function combineReviewFlushResults(earlier: ReviewFlushResult, later: ReviewFlushResult): ReviewFlushResult {
  return { persistedAttemptIds: [...earlier.persistedAttemptIds, ...later.persistedAttemptIds],
    conflictedAttemptIds: [...earlier.conflictedAttemptIds, ...later.conflictedAttemptIds],
    preparedContinuations: { ...earlier.preparedContinuations, ...later.preparedContinuations } };
}

function includeEarlierFlushSettlement(earlier: ReviewFlushResult, later: Promise<ReviewFlushResult>): Promise<ReviewFlushResult> {
  return later.then(result => combineReviewFlushResults(earlier, result), error => {
    if (!(error instanceof ReviewReplayError) || !error.flushResult) throw error;
    // The underlying flush may have other observers. Only this caller's error
    // includes the earlier generation; keep the original failure evidence intact.
    const combinedError = Object.assign(new Error(error.message, { cause: error.cause }), error) as ReviewReplayError;
    Object.setPrototypeOf(combinedError, Object.getPrototypeOf(error));
    combinedError.stack = error.stack;
    combinedError.flushResult = combineReviewFlushResults(earlier, error.flushResult);
    throw combinedError;
  });
}

// Only the Retry save action supplies an explicit attempt identity. Incidental
// queue refreshes and saving another card must never release terminal suppression.
export function flushPendingReviews(maximumReviews = Infinity, verifyReceiptFirst = false,
  explicitRetryAttemptId?: string, deferredAttemptIds?: ReadonlySet<string>, stopAfterAttemptId?: string): Promise<ReviewFlushResult> {
  if (activeFlush && stopAfterAttemptId) return waitForSubmittedAttempt(activeFlush, stopAfterAttemptId).catch(error => {
    const retained = savedReviews();
    const targetIndex = retained.findIndex(review => logicalAttemptId(review) === stopAfterAttemptId);
    const target = retained[targetIndex];
    const blockedByConflict = target && retained.slice(0, targetIndex).some(review => review.backendId === target.backendId && review.state === "conflicted");
    if (!explicitRetryAttemptId && !blockedByConflict) throw error;
    return error instanceof ReviewReplayError && error.flushResult ? error.flushResult : { persistedAttemptIds: [], conflictedAttemptIds: [] } as ReviewFlushResult;
  }).then(result => {
    if (attemptSettled(result, stopAfterAttemptId)) return result;
    return includeEarlierFlushSettlement(result,
      flushPendingReviews(maximumReviews, verifyReceiptFirst, explicitRetryAttemptId, deferredAttemptIds, stopAfterAttemptId));
  });
  if (activeFlush && explicitRetryAttemptId) {
    return activeFlush.catch(() => undefined).then(() => flushPendingReviews(maximumReviews, true, explicitRetryAttemptId, deferredAttemptIds));
  }
  if (activeFlush && activeFlushMaximumReviews !== Infinity && maximumReviews === Infinity) return activeFlush.then(result => {
    if (!recoverableReviews(undefined, deferredAttemptIds).length) return result;
    return includeEarlierFlushSettlement(result,
      flushPendingReviews(maximumReviews, verifyReceiptFirst, undefined, deferredAttemptIds));
  });
  if (!activeFlush) {
    currentFlushProgress = undefined;
    const retainedRetry = explicitRetryAttemptId && pendingReviews().find(review => logicalAttemptId(review) === explicitRetryAttemptId);
    if (retainedRetry && !recoverableReviews(explicitRetryAttemptId).some(review => logicalAttemptId(review) === explicitRetryAttemptId))
      return Promise.reject(new Error("An earlier result for this card needs attention before this result can retry."));
    activeFlushMaximumReviews = maximumReviews;
    // Reserve ownership before saving can synchronously emit a public event.
    // Reentrant callers observe this reservation's forwarded settlement.
    let resolveFlushReservation!: (result: ReviewFlushResult | PromiseLike<ReviewFlushResult>) => void;
    const flushReservation = new Promise<ReviewFlushResult>(resolve => { resolveFlushReservation = resolve; });
    // Ordinary callers receive the actual flush below; an unused reservation
    // must not create a second unhandled rejection when that flush fails.
    void flushReservation.catch(() => undefined);
    activeFlush = flushReservation;
    activeFlush = savePendingReviews(maximumReviews, verifyReceiptFirst || !!explicitRetryAttemptId, explicitRetryAttemptId, deferredAttemptIds, stopAfterAttemptId)
      .finally(() => { activeFlush = undefined; });
    resolveFlushReservation(activeFlush);
  }
  return activeFlush;
}
