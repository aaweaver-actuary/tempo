import type { OpeningEvidenceCheckpoint } from "../domain/opening-evidence";
import { confirmOperationResponse, FailedOperationError } from "./operation-status";
import { rejectOpeningEvidence } from "./opening-evidence-journal";

function definitiveEvidenceFailure(error: unknown): string | undefined {
  if (error instanceof FailedOperationError && /opening_evidence_(conflict|unavailable)/.test(error.message)) return error.message;
}

export async function saveEvidenceAwareReview(options: {
  endpoint: string; operationKey: string; body: Record<string, unknown>; completion?: OpeningEvidenceCheckpoint;
  evidenceRejected?: string; signal?: AbortSignal;
  aggregateOnly?: boolean;
  onEvidenceRejected: (message: string) => void | Promise<void>;
  request?: (url: string, options: RequestInit) => Promise<Response>;
}): Promise<Response> {
  const request = options.request ?? fetch;
  let rejection = options.evidenceRejected;
  const send = () => request(options.endpoint, { method: "POST", signal: options.signal,
    headers: { "Content-Type": "application/json", "Idempotency-Key": `${options.operationKey}${rejection || options.aggregateOnly ? ":aggregate-only" : ""}` },
    body: JSON.stringify({ ...options.body, ...(options.completion && !rejection && !options.aggregateOnly ? { opening_evidence_completion: options.completion } : {}) }),
  }).then(response => confirmOperationResponse(response, { signal: options.signal }));
  let response: Response;
  try { response = await send(); }
  catch (error) {
    const evidenceFailure = options.completion && definitiveEvidenceFailure(error);
    if (!evidenceFailure || rejection) throw error;
    rejection = evidenceFailure;
    await options.onEvidenceRejected(rejection);
    await rejectOpeningEvidence(options.completion!.attempt_id, rejection).catch(() => undefined);
    return send();
  }
  if (options.completion && !rejection && response.status === 409) {
    const result = await response.clone().json().catch(() => ({})) as {
      detail?: { code?: string; message?: string; aggregate_review_allowed?: boolean };
    };
    if (result.detail?.aggregate_review_allowed && result.detail.code?.startsWith("opening_evidence_")) {
      rejection = result.detail.message ?? "The opening evidence was rejected.";
      // Persist fallback identity before submitting it; a reload cannot switch delivery keys.
      await options.onEvidenceRejected(rejection);
      await rejectOpeningEvidence(options.completion.attempt_id, rejection).catch(() => undefined);
      response = await send();
    }
  }
  return response;
}
