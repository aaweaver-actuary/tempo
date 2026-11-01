import { learningFetch as fetch, type StudyClient } from "./study-connection";
import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { confirmOperationResponse, FailedOperationError, readOperationResponse } from "./operation-status";
import { rejectOpeningEvidence } from "./opening-evidence-journal";
import { createReviewTransport } from "./review-transport";

function definitiveEvidenceFailure(error: unknown): string | undefined {
  if (error instanceof FailedOperationError && /opening_evidence_(conflict|unavailable)/.test(error.message)) return error.message;
}

export async function saveEvidenceAwareReview(options: {
  endpoint: string; operationKey: string; body: Record<string, unknown>; completion?: OpeningEvidenceCheckpoint;
  evidenceRejected?: string; signal?: AbortSignal;
  aggregateOnly?: boolean;
  verifyReceiptFirst?: boolean;
  onEvidenceRejected: (message: string) => void | Promise<void>;
  request?: (url: string, options: RequestInit) => Promise<Response>;
  client?: StudyClient; requireCurrent?: () => void;
}): Promise<Response> {
  const request = options.request ?? options.client?.learningFetch ?? fetch;
  const binding = options.client ? createReviewTransport(options.client) : undefined;
  const requireCurrent = () => { binding?.requireCurrent(); options.requireCurrent?.(); };
  let rejection = options.evidenceRejected;
  const send = async (): Promise<Response> => {
    requireCurrent();
    if (options.verifyReceiptFirst) {
      const receipt = await readOperationResponse(`${options.operationKey}${rejection || options.aggregateOnly ? ":aggregate-only" : ""}`,
        { signal: options.signal, allowMissing: true, ...(options.client ? { client: options.client } : {}) });
      requireCurrent();
      if (receipt.status !== 404) return receipt;
    }
    requireCurrent();
    return request(options.endpoint, { method: "POST", signal: options.signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": `${options.operationKey}${rejection || options.aggregateOnly ? ":aggregate-only" : ""}` },
    body: JSON.stringify({ ...options.body, ...(options.completion && !rejection && !options.aggregateOnly ? { opening_evidence_completion: options.completion } : {}) }),
  }).then(response => {
    requireCurrent();
    return confirmOperationResponse(response, { signal: options.signal, ...(options.client ? { client: options.client } : {}) });
  }).then(response => { requireCurrent(); return response; });
  };
  let response: Response;
  try { response = await send(); }
  catch (error) {
    const evidenceFailure = options.completion && definitiveEvidenceFailure(error);
    if (!evidenceFailure || rejection) throw error;
    requireCurrent();
    rejection = evidenceFailure;
    await options.onEvidenceRejected(rejection);
    requireCurrent();
    await rejectOpeningEvidence(options.completion!.attempt_id, rejection).catch(() => undefined);
    requireCurrent();
    return send();
  }
  if (options.completion && !rejection && response.status === 409) {
    const result = await response.clone().json().catch(() => ({})) as {
      detail?: { code?: string; message?: string; aggregate_review_allowed?: boolean };
    };
    requireCurrent();
    if (result.detail?.aggregate_review_allowed && result.detail.code?.startsWith("opening_evidence_")) {
      rejection = result.detail.message ?? "The opening evidence was rejected.";
      // Persist fallback identity before submitting it; a reload cannot switch delivery keys.
      await options.onEvidenceRejected(rejection);
      requireCurrent();
      await rejectOpeningEvidence(options.completion.attempt_id, rejection).catch(() => undefined);
      requireCurrent();
      response = await send();
    }
  }
  return response;
}
