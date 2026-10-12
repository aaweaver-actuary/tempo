import receiptExamples from "../fixtures/controlled-browser/receipts.json" with { type: "json" };
export type ControlledRequest = { url: string; method: string; headers: Record<string, string>; body: unknown };
export type ControlledContract = {
  method: string; path: string | (() => string); query: Record<string, string>;
  validate?: (request: ControlledRequest) => void;
};
export function matchesContract(contract: ControlledContract, request: ControlledRequest, origin: string) {
  const url = new URL(request.url);
  const expectedPath = typeof contract.path === "function" ? contract.path() : contract.path;
  return url.origin === origin && request.method === contract.method && url.pathname === expectedPath &&
    JSON.stringify([...url.searchParams].sort()) === JSON.stringify(Object.entries(contract.query).sort());
}
export function validateReview(request: ControlledRequest, cardId: string, queueEntryId: number) {
  const body = request.body as Record<string, unknown> | null;
  const allowedFields = ["outcome", "guided", "queue_entry_id", "attempt_id", "recorded_at", "expected_review_id", "expected_revision", "prepared_next", "opening_evidence_completion"];
  if (!body || Object.keys(body).some(key => !allowedFields.includes(key)) ||
      body.outcome !== "correct" || body.guided !== false || body.queue_entry_id !== queueEntryId ||
      typeof body.attempt_id !== "string" || !body.attempt_id || typeof body.recorded_at !== "string" ||
      request.headers["idempotency-key"] !== `review-attempt:${body.attempt_id}` ||
      new URL(request.url).pathname !== `/api/cards/${cardId}/review`) {
    throw new Error(`Original review intent contract mismatch for ${cardId}`);
  }
}

export function receiptResponse(state: Exclude<keyof typeof receiptExamples, "review_request">, operationId: string) {
  return { ...receiptExamples[state], operation_id: operationId };
}
