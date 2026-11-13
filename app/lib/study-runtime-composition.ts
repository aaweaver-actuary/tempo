/** Explicit default Study composition. Construction remains lazy until session(). */
import { createStudyRuntime } from "../features/study/session-runtime";
import { defaultStudyClient } from "./study-connection";
import { studyIntentStore, isStudyIntentStoreCurrent, studyIntentRecords, updateStudyIntent, refreshStudyIntents } from "../../database/browser/study-intent-storage";
import { readRejectedOpeningReviews, writeRejectedOpeningReviews } from "../../database/browser/adapters/practice/review-archive";
import type { IntentRecord } from "@tempo/browser-intents";
import { IntentStorageError } from "@tempo/browser-intents";
import type { PreparedContinuation } from "./prepared-review-continuation";
import { createReviewDelivery, type PendingReview } from "./review-delivery";
import { createTeachingDelivery } from "./teaching-delivery";
import { createStudyCommandDelivery } from "./study-command-delivery";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { validLegacyReview, validConflict } from "./study-legacy-validation";
import { saveEvidenceAwareReview } from "./opening-evidence-review";
import { rejectOpeningEvidence } from "./opening-evidence-journal";
import { publishNotification } from "./notifications";

export type ReviewFlushResult = { persistedAttemptIds: string[]; conflictedAttemptIds: string[];
  preparedContinuations?: Record<string, PreparedContinuation> };

export const evidenceStorageErrors = new WeakSet<object>();
const reviewStorageErrors = new WeakSet<object>();
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

export async function settleExistingReviewDelivery(store: Awaited<ReturnType<typeof studyIntentStore>>, operationId: string,
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

export function savedReviews(): PendingReview[] {
  return studyIntentRecords("review").filter(record => record.state === "pending" || record.state === "conflicted")
    .map(record => {
      const review = { ...(record.data ?? record.payload) as PendingReview };
      delete review.deliveryStarted;
      return { ...review, ...(record.state === "conflicted" ? { state: "conflicted" as const } : {}) };
    });
}

export function readStoredReviews(): PendingReview[] {
  const stored = readRejectedOpeningReviews();
  if (!stored) return [];
  const parsed: unknown = JSON.parse(stored);
  if (!Array.isArray(parsed) || parsed.some(item => !validLegacyReview(item)))
    throw new Error("The saved training review is invalid. Restore your data before continuing.");
  return parsed as PendingReview[];
}

export async function replaceReview(review: PendingReview, replacement?: PendingReview,
  store?: Awaited<ReturnType<typeof studyIntentStore>>, requireCurrent: () => void = () => {}): Promise<void> {
  const change = (current: IntentRecord) => ({
    state: replacement?.state === "conflicted" ? "conflicted" as const : replacement ? "pending" as const : "committed" as const,
    data: replacement ? { ...(current.data as PendingReview), ...replacement } : current.data, receipt: replacement ? current.receipt : { persisted: true },
  });
  requireCurrent();
  if (store) { await store.update(`review:${logicalAttemptId(review)}`, change); requireCurrent(); await refreshStudyIntents(); requireCurrent(); }
  else await updateStudyIntent(`review:${logicalAttemptId(review)}`, change);
}


let runtime: ReturnType<typeof createStudyRuntime> | undefined;
export function defaultStudyRuntime() {
  if (runtime) return runtime;
  const operations = { confirmOriginalOperationResponse, readOperationResponse, FailedOperationError, PendingOperationError };
  const review = createReviewDelivery({ client: defaultStudyClient, getStore: studyIntentStore, isStoreCurrent: isStudyIntentStoreCurrent,
    savedReviews, readStoredReviews, writeRejectedOpeningReviews: (...args) => writeRejectedOpeningReviews(...args), replaceReview, settleExistingReviewDelivery,
    logicalAttemptId, reviewOperationId, validConflict, publishNotification: (...args) => publishNotification(...args), evidenceStorageErrors,
    ReviewReplayError, operations, saveEvidenceAwareReview, rejectOpeningEvidence });
  const teaching = createTeachingDelivery(defaultStudyClient, studyIntentStore, operations, isStudyIntentStoreCurrent);
  const command = createStudyCommandDelivery(defaultStudyClient, studyIntentStore, operations, isStudyIntentStoreCurrent);
  runtime = createStudyRuntime({ store: studyIntentStore, isCurrent: isStudyIntentStoreCurrent, adapters: [
    { kind: "review", disposition: "deliver", adapter: { deliver: review.deliverLegacyReview, checkReceipt: review.checkLegacyReviewReceipt } },
    { kind: "teaching", disposition: "deliver", adapter: teaching },
    { kind: "study-command", disposition: "deliver", adapter: command },
    { kind: "tactic-command", disposition: "deliver", adapter: command },
    { kind: "burial-command", disposition: "deliver", adapter: command },
    { kind: "settings-command", disposition: "deliver", adapter: command },
    // The existing command owner permits receipt-first recovery for started retired work.
    { kind: "defense-command", disposition: "deliver", adapter: command },
    { kind: "endgame-command", disposition: "deliver", adapter: command },
  ] });
  return runtime;
}
