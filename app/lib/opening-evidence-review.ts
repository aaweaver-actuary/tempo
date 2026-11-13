import { learningFetch as fetch, type StudyClient } from "./study-connection";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { confirmOriginalOperationResponse, FailedOperationError, PendingOperationError, readOperationResponse } from "./operation-status";
import { rejectOpeningEvidence } from "./opening-evidence-journal";
import { createReviewTransport } from "./review-transport";

const fallbackEvidenceCodes = new Set(["opening_evidence_conflict", "opening_evidence_unavailable"]);
// Older gateway receipts stored `str(HTTPException)` rather than `detail`.
// Accept only the exact producer-shaped Python dict representation, with an
// explicit True permission; never extract a token from arbitrary message text.
const legacyEvidenceDetail = /^409: \{'code': '(opening_evidence_(?:conflict|unavailable))', (?:'message': (?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"), )?'aggregate_review_allowed': True(?:, 'retryable': False)?\}$/;

function definitiveEvidenceFailure(error: unknown): string | undefined {
  if (!(error instanceof FailedOperationError) || error.retryable === true) return;
  // The receipt owner persisted status_code even before structured detail was
  // added. A message that merely resembles a 409 is not authoritative status.
  if (error.status !== 409) return;
  const detail = error.detail !== null && typeof error.detail === "object" && !Array.isArray(error.detail)
    ? error.detail as Record<string, unknown> : undefined;
  if (error.detail !== undefined && !detail) return;
  if (detail) {
    if (detail.aggregate_review_allowed !== true ||
        (Object.hasOwn(detail, "retryable") && detail.retryable !== false) ||
        typeof detail.code !== "string" || !fallbackEvidenceCodes.has(detail.code) ||
        (error.code !== undefined && error.code !== detail.code)) return;
  } else {
    const legacy = legacyEvidenceDetail.exec(error.message);
    if (!legacy || (error.code !== undefined && error.code !== legacy[1])) return;
  }
  return error.message;
}

type ReviewEvidenceOptions = {
  endpoint: string; operationKey: string; body: Record<string, unknown>; completion?: OpeningEvidenceCheckpoint;
  evidenceRejected?: string; signal?: AbortSignal;
  aggregateOnly?: boolean;
  /** Issued position attempts must retain their exact evidence envelope. */
  positionStudy?: boolean;
  verifyReceiptFirst?: boolean;
  /** The exact command whose terminal response was returned, including fallback suffix. */
  onConfirmedOperation?: (operationId: string) => void;
  onRejectedOperation?: (operationId: string, evidence: unknown) => Promise<void>;
  /** Only an already frozen fallback delivery may be resent while base proof is unavailable. */
  canRetryRetainedFallback?: (operationId: string) => Promise<boolean>;
  onEvidenceRejected: (message: string) => void | Promise<void>;
  request?: (url: string, options: RequestInit) => Promise<Response>;
} & (
  | { client: StudyClient; requireCurrent: () => void; rejectEvidence: typeof rejectOpeningEvidence }
  | { client?: undefined; requireCurrent?: undefined; rejectEvidence?: undefined }
);

export async function saveEvidenceAwareReview(options: ReviewEvidenceOptions): Promise<Response> {
  const request = options.request ?? options.client?.learningFetch ?? fetch;
  const binding = options.client ? createReviewTransport(options.client) : undefined;
  const rejectEvidence = options.rejectEvidence ?? rejectOpeningEvidence;
  const requireCurrent = () => { binding?.requireCurrent(); options.requireCurrent?.(); };
  let rejection = options.evidenceRejected;
  // Older retained fallback state carries a message, not an authoritative
  // base receipt. Re-read the original operation before disposing its journal.
  let baseRejectionUnconfirmed = false;
  if (rejection && options.completion && !options.positionStudy && !options.aggregateOnly && options.onRejectedOperation) {
    try {
      const original = await readOperationResponse(options.operationKey, { signal: options.signal, allowMissing: true, ...(options.client ? { client: options.client } : {}) });
      requireCurrent();
      if (original.status !== 404) {
        if (original.ok) options.onConfirmedOperation?.(options.operationKey);
        return original;
      }
      baseRejectionUnconfirmed = true;
    } catch (error) {
      requireCurrent();
      if (options.signal?.aborted) throw error;
      if (error instanceof FailedOperationError && definitiveEvidenceFailure(error))
        await options.onRejectedOperation(options.operationKey, { state: "failed", error: { message: error.message, status_code: error.status, code: error.code, retryable: error.retryable, detail: error.detail } });
      else baseRejectionUnconfirmed = true;
    }
    requireCurrent();
  }
  const send = async (): Promise<Response> => {
    requireCurrent();
    const sentOperationId = `${options.operationKey}${rejection || options.aggregateOnly ? ":aggregate-only" : ""}`;
    if (options.verifyReceiptFirst || baseRejectionUnconfirmed) {
      const receipt = await readOperationResponse(sentOperationId,
        { signal: options.signal, allowMissing: true, ...(options.client ? { client: options.client } : {}) });
      requireCurrent();
      if (receipt.status !== 404) {
        if (receipt.ok) options.onConfirmedOperation?.(sentOperationId);
        return receipt;
      }
      if (baseRejectionUnconfirmed && !await options.canRetryRetainedFallback?.(sentOperationId))
        throw new PendingOperationError(options.operationKey,
          "The original evidence rejection is unconfirmed. Keep the original review and retry its receipt.");
      requireCurrent();
    }
    requireCurrent();
    return request(options.endpoint, { method: "POST", signal: options.signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": sentOperationId },
    body: JSON.stringify({ ...options.body, ...(options.completion && !rejection && !options.aggregateOnly ? { opening_evidence_completion: options.completion } : {}) }),
  }).then(response => {
    requireCurrent();
    return confirmOriginalOperationResponse(response, sentOperationId, { signal: options.signal, ...(options.client ? { client: options.client } : {}) });
  }).then(response => {
    requireCurrent();
    if (response.ok) options.onConfirmedOperation?.(sentOperationId);
    return response;
  });
  };
  let response: Response;
  try { response = await send(); }
  catch (error) {
    const evidenceFailure = !options.positionStudy && options.completion && definitiveEvidenceFailure(error);
    if (!evidenceFailure || rejection) throw error;
    requireCurrent();
    rejection = evidenceFailure;
    await options.onEvidenceRejected(rejection);
    requireCurrent();
    if (error instanceof FailedOperationError && error.status === 409 && error.retryable !== true)
      await options.onRejectedOperation?.(options.operationKey, { state: "failed", error: { message: error.message, status_code: error.status, code: error.code, retryable: error.retryable, detail: error.detail } });
    requireCurrent();
    await rejectEvidence(options.completion!.attempt_id, rejection).catch(() => undefined);
    requireCurrent();
    return send();
  }
  if (!options.positionStudy && options.completion && !rejection && response.status === 409) {
    const result = await response.clone().json().catch(() => ({})) as {
      detail?: { code?: string; message?: string; aggregate_review_allowed?: boolean; retryable?: boolean };
    };
    requireCurrent();
    if (result.detail?.aggregate_review_allowed === true &&
        (result.detail.retryable === undefined || result.detail.retryable === false) &&
        typeof result.detail.code === "string" && fallbackEvidenceCodes.has(result.detail.code)) {
      rejection = result.detail.message ?? "The opening evidence was rejected.";
      // Persist fallback identity before submitting it; a reload cannot switch delivery keys.
      await options.onEvidenceRejected(rejection);
      requireCurrent();
      await options.onRejectedOperation?.(options.operationKey, { status: response.status, body: result });
      requireCurrent();
      await rejectEvidence(options.completion.attempt_id, rejection).catch(() => undefined);
      requireCurrent();
      response = await send();
    }
  }
  return response;
}
