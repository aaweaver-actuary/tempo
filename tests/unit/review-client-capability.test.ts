import { afterEach, expect, it, vi } from "vitest";
import type { StudyClient } from "../../app/lib/study-connection";
import { createReviewTransport } from "../../app/lib/review-transport";
import { saveEvidenceAwareReview } from "../../app/lib/opening-evidence-review";
import { closeStudyIntentStorage, initializeStudyIntentStorage, studyIntentStore } from "../../database/browser/study-intent-storage";
import { enqueuePendingReview, flushPendingReviews } from "../../app/lib/review-outbox";

function selectedClient(origin: string, learningFetch: typeof fetch) {
  return { origin, generation: 1, independent: true, learningFetch } as unknown as StudyClient;
}
afterEach(() => { vi.unstubAllGlobals(); closeStudyIntentStorage(); });

it("binds receipt-first and 202 confirmation to each selected review authority", async () => {
  const calls: string[] = [];
  const run = async (origin: string) => {
    const request = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push(`${origin}:${init?.method ?? "GET"}:${String(url)}`);
      if (init?.method === "POST") {
        expect(new Headers(init.headers).get("Idempotency-Key")).toBe("review-attempt:original");
        return Response.json({ operation_id: "review-attempt:original" }, { status: 202 });
      }
      if (calls.filter(call => call.startsWith(`${origin}:GET`)).length === 1) return new Response(null, { status: 404 });
      return Response.json({ operation_id: "review-attempt:original", state: "complete", response: { persisted: true } });
    });
    const client = selectedClient(origin, request), binding = createReviewTransport(client);
    const response = await saveEvidenceAwareReview({ endpoint: `${origin}/api/cards/c/review`, operationKey: "review-attempt:original",
      body: { attempt_id: "original" }, client, requireCurrent: binding.requireCurrent, rejectEvidence: vi.fn(async () => {}), verifyReceiptFirst: true, onEvidenceRejected: () => {} });
    expect(await response.json()).toEqual({ persisted: true });
  };
  await run("https://a.example"); await run("https://b.example");
  expect(calls.filter(call => call.includes("/api/operations/")).every(call => call.includes("review-attempt%3Aoriginal"))).toBe(true);
  expect(calls).toHaveLength(6);
});

it("does not settle a review in replacement storage after a pending POST", async () => {
  await initializeStudyIntentStorage(true);
  await enqueuePendingReview({ backendId: "card", queueEntryId: 101, attemptId: "original", outcome: "correct", guided: false });
  let release!: (value: Response) => void, entered!: () => void;
  const waiting = new Promise<Response>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  vi.stubGlobal("fetch", vi.fn(async () => { entered(); return waiting; }));
  const pending = flushPendingReviews();
  await started;
  closeStudyIntentStorage();
  await initializeStudyIntentStorage(true);
  release(Response.json({ persisted: true }));
  await expect(pending).rejects.toThrow();
  expect((await (await studyIntentStore()).get("review:original"))?.state).not.toBe("committed");
});

it("uses the original guided marker and review operation IDs through the default adapter", async () => {
  await initializeStudyIntentStorage(true);
  await enqueuePendingReview({ backendId: "guided-card", queueEntryId: 102, attemptId: "guided-original", outcome: "again", guided: true });
  const posts: string[] = [], receipts: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      const id = new Headers(init.headers).get("Idempotency-Key")!;
      posts.push(id);
      return Response.json({ operation_id: id }, { status: 202 });
    }
    const id = decodeURIComponent(String(url).split("/").at(-1)!);
    receipts.push(id);
    return Response.json({ state: "complete", response: { persisted: true } });
  }));
  await flushPendingReviews();
  expect(posts).toEqual(["queue-fail:guided-original", "review-attempt:guided-original"]);
  expect(receipts).toEqual(posts);
});

it("does not poll or send fallback after authority changes during the original response", async () => {
  let release!: () => void, entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const reading = new Promise<void>(resolve => { entered = resolve; });
  const request = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).toBe("POST");
    const response = new Response(null, { status: 202 });
    response.clone = () => ({ json: async () => { entered(); await gate; return { operation_id: "review-attempt:original" }; } }) as Response;
    return response;
  });
  const client = selectedClient("https://a.example", request), binding = createReviewTransport(client);
  const pending = saveEvidenceAwareReview({ endpoint: "https://a.example/api/cards/c/review", operationKey: "review-attempt:original",
    body: { attempt_id: "original" }, client, requireCurrent: binding.requireCurrent, rejectEvidence: vi.fn(async () => {}), onEvidenceRejected: () => { throw new Error("unexpected fallback"); } });
  await reading;
  Object.defineProperty(client, "generation", { value: 2 });
  release();
  await expect(pending).rejects.toThrow("Study authority changed");
  expect(request).toHaveBeenCalledTimes(1);
});

it("does not poll a guided marker or settle its review after reconnect during the marker POST", async () => {
  await initializeStudyIntentStorage(true);
  await enqueuePendingReview({ backendId: "marker-card", queueEntryId: 103, attemptId: "marker-original", outcome: "again", guided: true });
  let release!: (response: Response) => void, entered!: () => void;
  const waiting = new Promise<Response>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  const transport = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    expect(String(url)).toContain("/fail");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("queue-fail:marker-original");
    entered();
    return waiting;
  });
  vi.stubGlobal("fetch", transport);
  const pending = flushPendingReviews();
  const failed = expect(pending).rejects.toThrow("Study authority changed");
  await started;
  const { disconnectStudy } = await import("../../app/lib/study-connection");
  disconnectStudy();
  release(Response.json({ operation_id: "queue-fail:marker-original" }, { status: 202 }));
  await failed;
  expect(transport).toHaveBeenCalledTimes(1);
  expect((await (await studyIntentStore()).get("review:marker-original"))?.state).not.toBe("committed");
  expect(await (await studyIntentStore()).get("review-delivery:review-attempt:marker-original")).toBeUndefined();
});

it("uses the supplied evidence journal for a custom authority fallback", async () => {
  const ids: string[] = [];
  const request = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    ids.push(new Headers(init?.headers).get("Idempotency-Key")!);
    return ids.length === 1
      ? Response.json({ detail: { code: "opening_evidence_conflict", message: "stale evidence", aggregate_review_allowed: true } }, { status: 409 })
      : Response.json({ persisted: true });
  });
  const client = selectedClient("https://a.example", request), binding = createReviewTransport(client);
  const rejectEvidence = vi.fn(async () => {}), onEvidenceRejected = vi.fn(async () => {});
  const completion = { attempt_id: "original" } as Parameters<typeof saveEvidenceAwareReview>[0]["completion"];
  const result = await saveEvidenceAwareReview({ endpoint: "https://a.example/api/cards/c/review", operationKey: "review-attempt:original",
    body: { attempt_id: "original" }, completion, client, requireCurrent: binding.requireCurrent, rejectEvidence, onEvidenceRejected });
  expect((await result.json()).persisted).toBe(true);
  expect(ids).toEqual(["review-attempt:original", "review-attempt:original:aggregate-only"]);
  expect(rejectEvidence).toHaveBeenCalledWith("original", "stale evidence");
});
