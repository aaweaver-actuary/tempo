import receiptExamples from "../fixtures/controlled-browser/receipts.json" with { type: "json" };
export type ControlledRequest = { url: string; method: string; headers: Record<string, string>; body: unknown };
export type ControlledContract = {
  method: string; path: string | (() => string); query: Record<string, string | RegExp>;
  validate?: (request: ControlledRequest) => void;
};
export const browserActivityQuery = {
  session_id: /^[A-Za-z0-9_-]{1,80}$/,
  expires_in_ms: /^(?:[1-9]\d{0,2}|[12]\d{3}|3000)$/,
};
export function matchesContract(contract: ControlledContract, request: ControlledRequest, origin: string) {
  const url = new URL(request.url);
  const expectedPath = typeof contract.path === "function" ? contract.path() : contract.path;
  return url.origin === origin && request.method === contract.method && url.pathname === expectedPath &&
    [...url.searchParams].length === Object.keys(contract.query).length &&
    Object.entries(contract.query).every(([name,expected]) => {
      const values=url.searchParams.getAll(name);
      return values.length===1 && (typeof expected==='string' ? values[0]===expected : expected.test(values[0]));
    });
}
export function validateReview(request: ControlledRequest, cardId: string, queueEntryId: number) {
  const body = request.body as Record<string, unknown> | null;
  const allowedFields = ["outcome", "guided", "queue_entry_id", "attempt_id", "recorded_at", "expected_revision"];
  if (!body || Object.keys(body).some(key => !allowedFields.includes(key)) ||
      body.outcome !== "correct" || body.guided !== false || body.queue_entry_id !== queueEntryId ||
      typeof body.attempt_id !== "string" || !body.attempt_id || typeof body.recorded_at !== "string" || Date.parse(body.recorded_at) !== Date.parse("2026-09-18T16:00:00Z") ||
      ("expected_revision" in body && body.expected_revision !== 1) ||
      request.headers["idempotency-key"] !== `review-attempt:${body.attempt_id}` ||
      new URL(request.url).pathname !== `/api/cards/${cardId}/review`) {
    throw new Error(`Original review intent contract mismatch for ${cardId}`);
  }
}

export function receiptResponse(state: Exclude<keyof typeof receiptExamples, "review_request">, operationId: string) {
  return { ...receiptExamples[state], operation_id: operationId };
}
