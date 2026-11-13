import { createReviewDelivery, type PendingReview, type ReviewConflictInformation, type LegacyReviewReceipt } from "./review-delivery";
export type { PendingReview, ReviewConflictInformation } from "./review-delivery";
import { readRejectedOpeningReviews, writeRejectedOpeningReviews } from "../../database/browser/adapters/practice/review-archive";
import { defaultStudyClient } from "./study-connection";
import { createReviewTransport, ReviewAuthorityChangedError } from "./review-transport";
import type { StudySession } from "@tempo/study-session";
import { registerStudyDeliveryAdapter, studySession } from "./study-session-runtime";
import type { IntentRecord } from "@tempo/browser-intents";
import { studyIntentRecords, studyIntentStore, isStudyIntentStoreCurrent, updateStudyIntent, refreshStudyIntents, immutableReviewPayload } from "../../database/browser/study-intent-storage";
import { confirmReviewSaveNotice, reportReviewSaveStatus } from "./review-save-notice";
import type { PreparedContinuation } from "./prepared-review-continuation";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { publishNotification, notifications, resolveNotification } from "./notifications";
import { clearTrainingFailureAfterReview } from "./training-failure-outbox";

import { validLegacyReview, validConflict } from "./study-legacy-validation";
import { definitiveEvidenceFailure, saveEvidenceAwareReview } from "./opening-evidence-review";
import { acknowledgeOpeningReview, rejectOpeningEvidence, retainOpeningEvidenceForStorageFallback } from "./opening-evidence-journal";
import { IntentStorageError } from "@tempo/browser-intents";

export type ReviewFlushResult = { persistedAttemptIds: string[]; conflictedAttemptIds: string[];
  preparedContinuations?: Record<string, PreparedContinuation> };

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

function reviewOperationId(review: PendingReview): string {
  const attemptId = logicalAttemptId(review);
  const operation = review.state === "reconciling" ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}`;
  return `${operation}${review.evidenceRejected || review.evidenceFallbackReason ? ":aggregate-only" : ""}`;
}

async function settleExistingReviewDelivery(store: Awaited<ReturnType<typeof studyIntentStore>>, operationId: string,
  attemptId: string, receipt: unknown, requireCurrent: () => void, state: "committed" | "discarded" = "committed"): Promise<void> {
  const key = `review-delivery:${operationId}`;
  requireCurrent();
  const existing = await store.get(key);
  requireCurrent();
  if (!existing || existing.kind !== "review-delivery" || existing.requestId !== operationId || existing.attemptId !== attemptId || existing.state !== "pending") return;
  await store.update(key, current => current.kind === "review-delivery" && current.requestId === operationId &&
    current.attemptId === attemptId && current.state === "pending" ? { ...current, state, receipt } : current);
  requireCurrent();
}

/** Pending delivery keys are inspected only by the existing idle recovery hook. */
export function pendingReviewDeliveryKeys(): string[] {
  // The active projection omits committed parents. A live parent already owns
  // its delivery, so do not let it compete with its own retry deadline.
  const active = new Set(studyIntentRecords("review").map(record => record.key));
  return studyIntentRecords("review-delivery").filter(record => record.state === "pending" &&
    !active.has(`review:${record.attemptId}`)).map(record => record.key);
}

/** Recover one orphan from its own authority receipt; never infer a child result from its parent. */
export async function recoverCommittedReviewDelivery(key: string, signal: AbortSignal): Promise<"settled" | "hold" | "ineligible"> {
  const transport = createReviewTransport(defaultStudyClient);
  const store = await studyIntentStore();
  const requireCurrent = () => {
    transport.requireCurrent();
    if (!isStudyIntentStoreCurrent(store)) throw new ReviewAuthorityChangedError();
  };
  requireCurrent();
  const delivery = await store.get(key);
  requireCurrent();
  if (!delivery || delivery.kind !== "review-delivery" || delivery.state !== "pending" ||
      key !== `review-delivery:${delivery.requestId}`) return "ineligible";
  const attemptId = delivery.attemptId;
  const payload = delivery.payload as { parentKey?: unknown; headers?: Record<string, unknown>; body?: Record<string, unknown> } | undefined;
  if (payload?.parentKey !== `review:${attemptId}` || payload.headers?.["Idempotency-Key"] !== delivery.requestId ||
      !payload.body || typeof payload.body !== "object" || payload.body.attempt_id !== attemptId) return "ineligible";
  const parent = await store.get(`review:${attemptId}`);
  requireCurrent();
  if (!parent || parent.kind !== "review" || parent.state !== "committed" ||
      (parent.receipt as { persisted?: unknown } | undefined)?.persisted !== true) return "ineligible";
  const review = (parent.data ?? parent.payload) as PendingReview;
  if (!review || typeof review !== "object" || logicalAttemptId(review) !== attemptId || typeof review.backendId !== "string") return "ineligible";
  const base = `review-attempt:${attemptId}`, reconciliation = `review-reconcile:${attemptId}:`;
  const isBase = delivery.requestId === base || delivery.requestId === `${base}:aggregate-only`;
  const isReconciliation = delivery.requestId.startsWith(reconciliation) &&
    /^[1-9][0-9]*(?::aggregate-only)?$/.test(delivery.requestId.slice(reconciliation.length));
  if (!isBase && !isReconciliation) return "ineligible";
  const expectedBody = { outcome: review.outcome, guided: review.guided,
    queue_entry_id: review.queueEntryId, attempt_id: attemptId,
    ...(review.completedAt ? { recorded_at: review.completedAt } : {}),
    ...(review.expectedRevision ? { expected_revision: review.expectedRevision } : {}),
    ...(review.preparedNext ? { prepared_next: review.preparedNext } : {}),
    ...(review.openingEvidenceCompletion && !delivery.requestId.endsWith(":aggregate-only")
      ? { opening_evidence_completion: review.openingEvidenceCompletion } : {}),
  };
  if (JSON.stringify(payload.body) !== JSON.stringify(expectedBody)) return "ineligible";
  const endpoint = `${transport.origin}/api/cards/${encodeURIComponent(review.backendId)}/review${isReconciliation ? "/reconcile" : ""}`;
  if (delivery.endpoint !== endpoint) return "ineligible";
  try {
    const response = await readOperationResponse(delivery.requestId, { allowMissing: true, signal, client: transport.client });
    requireCurrent();
    if (response.status === 404) return "hold";
    const receipt = await response.json() as { persisted?: unknown };
    requireCurrent();
    if (receipt?.persisted !== true) return "hold";
    await settleExistingReviewDelivery(store, delivery.requestId, attemptId, receipt, requireCurrent);
    await refreshStudyIntents();
    requireCurrent();
    return "settled";
  } catch (error) {
    requireCurrent();
    if (signal.aborted) return "hold";
    if (!delivery.requestId.endsWith(":aggregate-only") && review.openingEvidenceCompletion &&
        error instanceof FailedOperationError && definitiveEvidenceFailure(error)) {
      await settleExistingReviewDelivery(store, delivery.requestId, attemptId,
        { state: "failed", error: { message: error.message, status_code: error.status, code: error.code,
          retryable: error.retryable, detail: error.detail } }, requireCurrent, "discarded");
      await refreshStudyIntents();
      requireCurrent();
      return "settled";
    }
    return "hold";
  }
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

async function replaceReview(review: PendingReview, replacement?: PendingReview,
  store?: Awaited<ReturnType<typeof studyIntentStore>>, requireCurrent: () => void = () => {}): Promise<void> {
  const change = (current: IntentRecord) => ({
    state: replacement?.state === "conflicted" ? "conflicted" as const : replacement ? "pending" as const : "committed" as const,
    data: replacement ? { ...(current.data as PendingReview), ...replacement } : current.data, receipt: replacement ? current.receipt : { persisted: true },
  });
  requireCurrent();
  if (store) { await store.update(`review:${logicalAttemptId(review)}`, change); requireCurrent(); await refreshStudyIntents(); requireCurrent(); }
  else await updateStudyIntent(`review:${logicalAttemptId(review)}`, change);
}

export async function enqueuePendingReview(review: PendingReview): Promise<string> {
  if (review.positionStudy && (!review.attemptId || !review.openingEvidenceCompletion ||
      review.openingEvidenceCompletion.attempt_id !== review.attemptId ||
      review.openingEvidenceCompletion.terminal?.state !== "complete" ||
      review.openingEvidenceCompletion.queue_entry_id !== review.queueEntryId ||
      review.openingEvidenceCompletion.manifest.card_id !== review.backendId ||
      review.openingEvidenceCompletion.manifest.card_revision !== review.expectedRevision))
    throw new Error("The issued position attempt and its completed evidence are required to save this review.");
  const store = await studyIntentStore();
  const retained = review.attemptId ? await store.get(`review:${review.attemptId}`) : undefined;
  const previous = retained ? (retained.data ?? retained.payload) as PendingReview : savedReviews().find(item => review.attemptId
    ? logicalAttemptId(item) === review.attemptId
    : item.queueEntryId === review.queueEntryId && item.backendId === review.backendId);
  if (previous) {
    for (const field of ["backendId", "queueEntryId", "outcome", "guided", "completedAt", "expectedRevision", "openingEvidenceCompletion", "preparedNext", "positionStudy"] as const)
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
    if (normalizedReview.positionStudy || !(cause instanceof DOMException) || !["QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"].includes(cause.name) || !normalizedReview.openingEvidenceCompletion) throw error;
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
  await replaceReview(review, { ...review, state: review.positionStudy ? "pending" : "reconciling", conflict: undefined, automaticRecoverySuppressed: undefined,
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


const { deliverLegacyReview, checkLegacyReviewReceipt } = createReviewDelivery({
  client: defaultStudyClient, getStore: studyIntentStore, isStoreCurrent: isStudyIntentStoreCurrent,
  savedReviews, readStoredReviews, writeRejectedOpeningReviews: (...args) => writeRejectedOpeningReviews(...args), replaceReview, settleExistingReviewDelivery,
  logicalAttemptId, reviewOperationId, validConflict, publishNotification: (...args) => publishNotification(...args), evidenceStorageErrors,
  ReviewReplayError, operations: { confirmOriginalOperationResponse, readOperationResponse, FailedOperationError, PendingOperationError },
  saveEvidenceAwareReview, rejectOpeningEvidence,
});
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
        if (error.cause instanceof ReviewAuthorityChangedError) throw error;
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
        `${defaultStudyClient.origin}/api/cards/${review.backendId}/review`, review, undefined, undefined, true, { cause: error });
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
