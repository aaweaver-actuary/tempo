import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { StudyClient } from "./study-client";
import type { PreparedNextIdentity, PreparedContinuation } from "./prepared-review-continuation";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { createReviewTransport, ReviewAuthorityChangedError } from "./review-transport";

export type ReviewConflictInformation = { code: string; message: string; retryable: boolean };
export type PendingReview = {
  backendId: string;
  queueEntryId: number;
  outcome: "again" | "correct";
  guided: boolean;
  attemptId?: string;
  completedAt?: string;
  openingEvidenceCompletion?: OpeningEvidenceCheckpoint;
  positionStudy?: true;
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

export type LegacyReviewReceipt = { persisted?: boolean; conflict?: unknown; warning?: string; prepared_continuation?: PreparedContinuation;
  competing_review?: { outcome?: string | null; completed_at?: string | null } };

type ReplayError = Error & { status?: number; code?: string; retryable: boolean };
type ReplayErrorConstructor = new (message: string, endpoint: string, review: PendingReview, status?: number, code?: string, retryable?: boolean, options?: ErrorOptions) => ReplayError;
type Hooks = {
  client: StudyClient; getStore: () => Promise<IntentStore>; isStoreCurrent: (store: IntentStore) => boolean;
  savedReviews: () => PendingReview[]; readStoredReviews: () => PendingReview[];
  writeRejectedOpeningReviews: typeof import("../../database/browser/adapters/practice/review-archive").writeRejectedOpeningReviews;
  replaceReview: (review: PendingReview, replacement?: PendingReview, store?: IntentStore, requireCurrent?: () => void) => Promise<void>;
  settleExistingReviewDelivery: (store: IntentStore, operationId: string, attemptId: string, receipt: unknown, requireCurrent: () => void, state?: "committed" | "discarded") => Promise<void>;
  logicalAttemptId: (review: PendingReview) => string; reviewOperationId: (review: PendingReview) => string;
  validConflict: typeof import("./study-legacy-validation").validConflict;
  publishNotification: typeof import("./notifications").publishNotification;
  evidenceStorageErrors: WeakSet<object>; ReviewReplayError: ReplayErrorConstructor;
  operations: Pick<typeof import("./operation-status"), "confirmOriginalOperationResponse" | "readOperationResponse" | "FailedOperationError" | "PendingOperationError">;
  saveEvidenceAwareReview: typeof import("./opening-evidence-review").saveEvidenceAwareReview;
  rejectOpeningEvidence: typeof import("./opening-evidence-journal").rejectOpeningEvidence;
  getBinding: (attemptId: string) => { requireCurrent: () => void; offline: import("../../database/browser/offline-training-storage").OfflineDatabaseBinding } | undefined;
};

export function createReviewDelivery(hooks: Hooks) {
  const { client, getStore, isStoreCurrent, savedReviews, readStoredReviews, writeRejectedOpeningReviews, replaceReview,
    settleExistingReviewDelivery, logicalAttemptId, reviewOperationId, validConflict, publishNotification, evidenceStorageErrors,
    ReviewReplayError, operations, saveEvidenceAwareReview, rejectOpeningEvidence, getBinding } = hooks;
  const { confirmOriginalOperationResponse, readOperationResponse, FailedOperationError } = operations;
  const reviewRequestTimeoutMs = 15_000;
  type ReviewReplayError = ReplayError;
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
  const detail = body.detail !== null && typeof body.detail === "object" && !Array.isArray(body.detail)
    ? body.detail as { message?: unknown; code?: unknown; retryable?: unknown } : undefined;
  const message = typeof body.detail === "string" ? body.detail : typeof detail?.message === "string"
    ? detail.message : `Local service returned HTTP ${response.status}.`;
  const code = typeof detail?.code === "string" ? detail.code : body.code;
  const retryable = typeof detail?.retryable === "boolean" ? detail.retryable : body.retryable;
  return new ReviewReplayError(message, endpoint, review, response.status, code, retryable ?? response.status >= 500);
}

async function sendReview(review: PendingReview, transport: ReturnType<typeof createReviewTransport>, store: IntentStore, requireCurrent: () => void, verifyReceiptFirst = false, parentSignal?: AbortSignal): Promise<Response> {
  const attemptId = logicalAttemptId(review);
  const reconciling = review.state === "reconciling";
  const endpoint = `${transport.origin}/api/cards/${encodeURIComponent(review.backendId)}/review${reconciling ? "/reconcile" : ""}`;
  let failureMarkerSent = false;
  let confirmedOperationId: string | undefined;
  const response = await withReviewSaveDeadline(endpoint, review, signal => saveEvidenceAwareReview({
    endpoint, signal, client: transport.client, requireCurrent, rejectEvidence: (id, message) => { const binding = getBinding(attemptId); return binding ? rejectOpeningEvidence(id, message, binding.offline) : rejectOpeningEvidence(id, message); }, request: async (url, options) => {
      requireCurrent();
      // Receipt-first recovery reaches this callback only when replay is necessary.
      if (!review.positionStudy && review.guided && !reconciling && !failureMarkerSent) {
        failureMarkerSent = true;
        const failureEndpoint = `${transport.origin}/api/queue/entries/${review.queueEntryId}/fail`;
        try {
          const response = await withReviewSaveDeadline(failureEndpoint, review, async signal => {
            const marker = await transport.client.learningFetch(failureEndpoint, {
              method: "POST", headers: { "Idempotency-Key": `queue-fail:${attemptId}`, "Content-Type": "application/json" },
              body: JSON.stringify({ card_id: review.backendId, expected_revision: review.expectedRevision }), signal,
            });
            requireCurrent();
            const confirmed = await confirmOriginalOperationResponse(marker, `queue-fail:${attemptId}`, { signal, client: transport.client });
            requireCurrent();
            return confirmed;
          }, parentSignal);
          requireCurrent();
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
      requireCurrent();
      const existing = await store.get(deliveryKey);
      requireCurrent();
      await store.put({ key: deliveryKey, kind: "review-delivery", attemptId, requestId: operationId,
        endpoint: url, revision: review.expectedRevision, createdAt: existing?.createdAt ?? review.completedAt ?? "legacy-time-unavailable",
        payload: { parentKey: `review:${attemptId}`, headers: options.headers, body: JSON.parse(String(options.body)) } }, undefined, { requireCurrent });
      requireCurrent();
      const response = await transport.client.learningFetch(url, options);
      requireCurrent();
      return response;
    },
    operationKey: reconciling ? `review-reconcile:${attemptId}:${review.reconciliationSequence ?? 1}` : `review-attempt:${attemptId}`,
    completion: review.openingEvidenceCompletion, evidenceRejected: review.evidenceRejected, positionStudy: review.positionStudy,
    verifyReceiptFirst,
    onConfirmedOperation: operationId => { confirmedOperationId = operationId; },
    onRejectedOperation: (operationId, evidence) => settleExistingReviewDelivery(store, operationId, attemptId, evidence, requireCurrent, "discarded"),
    canRetryRetainedFallback: async operationId => {
      requireCurrent();
      const retained = await store.get(`review-delivery:${operationId}`);
      requireCurrent();
      const payload = retained?.payload as { parentKey?: unknown; headers?: Record<string, unknown>; body?: unknown } | undefined;
      const expectedBody = { outcome: review.outcome, guided: review.guided,
        queue_entry_id: review.queueEntryId, attempt_id: attemptId,
        ...(review.completedAt ? { recorded_at: review.completedAt } : {}),
        ...(review.expectedRevision ? { expected_revision: review.expectedRevision } : {}),
        ...(review.preparedNext ? { prepared_next: review.preparedNext } : {}),
      };
      return retained?.kind === "review-delivery" && retained.requestId === operationId && retained.attemptId === attemptId &&
        retained.endpoint === endpoint && retained.state === "pending" && payload?.parentKey === `review:${attemptId}` &&
        payload.headers?.["Idempotency-Key"] === operationId && JSON.stringify(payload.body) === JSON.stringify(expectedBody);
    },
    aggregateOnly: review.evidenceFallbackReason === "local_storage_quota",
    onEvidenceRejected: async (message) => {
      requireCurrent();
      const updated = savedReviews().map((item) =>
        item.attemptId === attemptId ? { ...item, evidenceRejected: message } : item);
      // Required: a reload must retain the aggregate-only delivery identity.
      try {
        const latest = updated.find(item => item.attemptId === attemptId);
        if (latest) await replaceReview(latest, latest, store, requireCurrent);
        requireCurrent();
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
  // Only a direct successful result or the original operation's confirmed
  // receipt can settle the delivery. A 202 body is never a review receipt.
  const receipt = await response.clone().json().catch(() => undefined) as { persisted?: boolean } | undefined;
  requireCurrent();
  if (receipt?.persisted === true && confirmedOperationId)
    await settleExistingReviewDelivery(store, confirmedOperationId, attemptId, receipt, requireCurrent);
  requireCurrent();
  return response;
}



async function deliverLegacyReview(record: IntentRecord, signal: AbortSignal) {
  const transport = createReviewTransport(client);
  const store = await getStore();
  const requireCurrent = () => {
    transport.requireCurrent();
    getBinding(logicalAttemptId((record.data ?? record.payload) as PendingReview))?.requireCurrent();
    if (!isStoreCurrent(store)) throw new ReviewAuthorityChangedError();
  };
  requireCurrent();
  let review = (record.data ?? record.payload) as PendingReview;
  const attemptId = logicalAttemptId(review);
  const records = savedReviews();
  const currentIndex = records.findIndex(item => logicalAttemptId(item) === attemptId);
  if (records.slice(0, currentIndex).some(item => item.state === "conflicted" && item.backendId === review.backendId))
    return { state: "conflicted" as const, data: { ...review, state: "conflicted", conflict: {
      code: "earlier_review_conflict", message: "An earlier result for this card needs review first.", retryable: false } } };
  const verifyReceiptFirst = review.deliveryStarted === true;
  await replaceReview(review, { ...review, deliveryStarted: true }, store, requireCurrent);
  requireCurrent();
  let response: Response;
  try { response = await sendReview(review, transport, store, requireCurrent, verifyReceiptFirst, signal); }
  catch (error) {
    if (review.positionStudy && error instanceof ReviewReplayError && error.status === 409 && !error.retryable) {
      return { state: "conflicted" as const, data: { ...review, state: "conflicted", conflict: {
        code: error.code ?? "position_attempt_conflict", message: error.message, retryable: false } } };
    }
    // An evidence rejection without an authorized aggregate fallback must not
    // be converted into a different reconciliation operation. Its original
    // command and evidence remain available for explicit recovery.
    if (error instanceof ReviewReplayError &&
        (error.code?.startsWith("opening_evidence_") || /\bopening_evidence_(?:conflict|unavailable)\b/.test(error.message))) throw error;
    if (!(error instanceof ReviewReplayError) || error.status !== 409 || error.retryable || review.state === "reconciling") throw error;
    requireCurrent();
    review = { ...savedReviews().find(item => logicalAttemptId(item) === attemptId)!, state: "reconciling", reconciliationSequence: 1 };
    await replaceReview(review, review, store, requireCurrent);
    requireCurrent();
    response = await sendReview(review, transport, store, requireCurrent, false, signal);
  }
  requireCurrent();
  const persisted = await response.clone().json() as LegacyReviewReceipt;
  requireCurrent();
  if (review.state === "reconciling" && persisted.persisted === false && validConflict(persisted.conflict)) {
    if (persisted.conflict.retryable) throw new ReviewReplayError(persisted.conflict.message, response.url, review, 409, persisted.conflict.code, true);
    return { state: "conflicted" as const, receipt: persisted, data: { ...review, state: "conflicted", conflict: persisted.conflict } };
  }
  if (persisted.persisted !== true) throw new ReviewReplayError("The computer did not confirm this review. Retry saving it.",
    `${transport.origin}/api/cards/${review.backendId}/review`, review);
  return { state: "committed" as const, receipt: persisted };
}

async function checkLegacyReviewReceipt(record: IntentRecord, signal: AbortSignal) {
  const transport = createReviewTransport(client);
  const store = await getStore();
  const requireCurrent = () => {
    transport.requireCurrent();
    getBinding(logicalAttemptId((record.data ?? record.payload) as PendingReview))?.requireCurrent();
    if (!isStoreCurrent(store)) throw new ReviewAuthorityChangedError();
  };
  requireCurrent();
  const review = (record.data ?? record.payload) as PendingReview;
  const attemptId = logicalAttemptId(review);
  const operationId = reviewOperationId(review);
  const response = await readOperationResponse(operationId, { allowMissing: true, signal, client: transport.client });
  requireCurrent();
  if (response.status === 404) return undefined;
  const receipt = await response.json() as LegacyReviewReceipt;
  requireCurrent();
  if (receipt.persisted === true) {
    await settleExistingReviewDelivery(store, operationId, attemptId, receipt, requireCurrent);
    return { state: "committed" as const, receipt };
  }
  if (receipt.persisted === false && validConflict(receipt.conflict) && !receipt.conflict.retryable)
    return { state: "conflicted" as const, receipt, data: { ...review, state: "conflicted", conflict: receipt.conflict } };
  return { state: "pending" as const };
}
  return { deliverLegacyReview, checkLegacyReviewReceipt };
}
