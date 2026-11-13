import { learningFetch as fetch, type StudyClient } from "./study-connection";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { confirmOriginalOperationResponse, FailedOperationError, readOperationResponse } from "./operation-status";
import { rejectOpeningEvidence } from "./opening-evidence-journal";
import { createReviewTransport } from "./review-transport";

function definitiveEvidenceFailure(error: unknown): string | undefined {
  if (error instanceof FailedOperationError && /opening_evidence_(conflict|unavailable)/.test(error.message)) return error.message;
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
  if (rejection && options.completion && !options.positionStudy && !options.aggregateOnly && options.onRejectedOperation) {
    try {
      await readOperationResponse(options.operationKey, { signal: options.signal, allowMissing: true, ...(options.client ? { client: options.client } : {}) });
    } catch (error) {
      requireCurrent();
      if (options.signal?.aborted) throw error;
      if (error instanceof FailedOperationError && error.status === 409 && error.retryable !== true && definitiveEvidenceFailure(error))
        await options.onRejectedOperation(options.operationKey, { state: "failed", error: { message: error.message, status_code: error.status, code: error.code, retryable: error.retryable, detail: error.detail } });
    }
    requireCurrent();
  }
  const send = async (): Promise<Response> => {
    requireCurrent();
    const sentOperationId = `${options.operationKey}${rejection || options.aggregateOnly ? ":aggregate-only" : ""}`;
    if (options.verifyReceiptFirst) {
      const receipt = await readOperationResponse(sentOperationId,
        { signal: options.signal, allowMissing: true, ...(options.client ? { client: options.client } : {}) });
      requireCurrent();
      if (receipt.status !== 404) {
        if (receipt.ok) options.onConfirmedOperation?.(sentOperationId);
        return receipt;
      }
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
      detail?: { code?: string; message?: string; aggregate_review_allowed?: boolean };
    };
    requireCurrent();
    if (result.detail?.aggregate_review_allowed && result.detail.code?.startsWith("opening_evidence_")) {
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
