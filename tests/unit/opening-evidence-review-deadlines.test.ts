import { afterEach, expect, it, vi } from "vitest";
import { evidenceCompletion, openingEvidenceStorage } from "../fixtures/opening-evidence-storage";
import { localDayKey } from "../../app/utils/local";
import type { PreparedTraining } from "../../app/lib/offline-training";

const browserStorage = localStorage;
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); browserStorage.clear(); });

for (const lane of ["online", "phone"] as const) {
it.each([false, true])(`AS-16 ${lane} review receipt polling remains bounded after HTTP 202 (aggregateOnly=%s)`, async aggregateOnly => {
  const state = await openingEvidenceStorage();
  vi.stubGlobal("localStorage", browserStorage); browserStorage.clear();
  const completion = evidenceCompletion("bounded-review");
  const offline = await import("../../app/lib/offline-training");
  const outbox = await import("../../app/lib/review-outbox");
  if (lane === "online") outbox.enqueuePendingReview({ backendId: completion.manifest.card_id, queueEntryId: 101,
    attemptId: completion.attempt_id, completedAt: completion.terminal!.ended_at, outcome: "correct", guided: false,
    ...(aggregateOnly ? { evidenceFallbackReason: "local_storage_quota" } : { openingEvidenceCompletion: completion }) });
  else state.stores.training.set("prepared-daily-queue", { localDate: localDayKey(), preparedAt: completion.started_at,
    nextTemporaryId: 1000000, cards: [], attempts: [{ localEntryId: 101, cardId: completion.manifest.card_id,
      outcome: "correct", guided: false, completedAt: completion.terminal!.ended_at, expectedReviewId: 0, expectedRevision: 3,
      attemptId: completion.attempt_id, ...(aggregateOnly ? { openingEvidenceFallbackReason: "local_storage_quota" }
        : { openingEvidenceCompletion: completion }) }] } satisfies PreparedTraining);
  const original = structuredClone(lane === "online" ? outbox.pendingReviews() : state.stores.training.get("prepared-daily-queue"));
  vi.useFakeTimers();
  const posts: RequestInit[] = [], receipts: (RequestInit | undefined)[] = [];
  let releaseReceipt!: (response: Response) => void;
  let stalled = true;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBeNull();
    if (init?.method === "POST") { posts.push(init); return Promise.resolve(Response.json({ operation_id: "review-operation" }, { status: 202 })); }
    expect(url).toContain("/api/operations/review-operation"); receipts.push(init);
    if (!stalled) return Promise.resolve(Response.json({ state: "complete", response: { persisted: true, review_id: 81 } }));
    return new Promise<Response>((resolve, reject) => {
      releaseReceipt = resolve;
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Review receipt aborted", "AbortError")), { once: true });
    });
  }));
  const flush = () => lane === "online" ? outbox.flushPendingReviews() : offline.replayOfflineAttempts();
  let failure: unknown; let settled = false;
  const first = flush().catch(error => { failure = error; }).finally(() => { settled = true; });
  try {
    await vi.waitFor(() => expect(receipts).toHaveLength(1));
    await vi.advanceTimersByTimeAsync(lane === "online" ? 15_000 : 5_000);
    expect(settled).toBe(true);
    expect(failure).toBeInstanceOf(Error);
    expect(receipts[0]?.signal).toBe(posts[0].signal);
    expect(posts[0].signal?.aborted).toBe(true);
    expect(lane === "online" ? outbox.pendingReviews() : state.stores.training.get("prepared-daily-queue")).toEqual(original);
    stalled = false; await flush(); await flush();
    expect(posts).toHaveLength(2); expect(posts[1].body).toBe(posts[0].body);
    const key = `${lane === "online" ? "review-attempt" : "phone-reconcile"}:${completion.attempt_id}${aggregateOnly ? ":aggregate-only" : ""}`;
    expect(posts.map(post => new Headers(post.headers).get("Idempotency-Key"))).toEqual([key, key]);
    if (lane === "online") expect(outbox.pendingReviews()).toEqual([]);
    else expect((state.stores.training.get("prepared-daily-queue") as PreparedTraining).attempts[0])
      .toMatchObject({ attemptId: completion.attempt_id, serverAcknowledged: true, serverReviewId: 81 });
  } finally {
    releaseReceipt?.(Response.json({ state: "complete", response: { persisted: true, review_id: 81 } })); await first;
  }
});
}


it("AS-16 guided review failure receipt shares the foreground review deadline", async () => {
  await openingEvidenceStorage(); vi.stubGlobal("localStorage", browserStorage); browserStorage.clear();
  const outbox = await import("../../app/lib/review-outbox");
  outbox.enqueuePendingReview({ backendId: "shadow-card", queueEntryId: 101, attemptId: "guided-review", guided: true, outcome: "again" });
  const original = outbox.pendingReviews(); vi.useFakeTimers();
  const posts: RequestInit[] = []; let retry = false; let receiptSignal: AbortSignal | null | undefined;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-Tempo-Work-Class")).toBeNull();
    if (init?.method === "POST") { posts.push(init);
      return url.endsWith("/fail") ? Response.json({ operation_id: "guided-failure" }, { status: 202 }) : Response.json({ persisted: true }); }
    receiptSignal = init?.signal;
    if (retry) return Response.json({ state: "complete", response: { persisted: true } });
    const receipt = Promise.withResolvers<Response>();
    init?.signal?.addEventListener("abort", () => receipt.reject(new DOMException("Receipt aborted", "AbortError")), { once: true });
    return receipt.promise;
  }));
  const first = outbox.flushPendingReviews(); const failed = expect(first).rejects.toThrow("timed out after 15 seconds");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2)); await vi.advanceTimersByTimeAsync(15_000); await failed;
  expect(receiptSignal).toBe(posts[0].signal); expect(receiptSignal?.aborted).toBe(true); expect(outbox.pendingReviews()).toEqual(original);
  retry = true; await outbox.flushPendingReviews(); expect(outbox.pendingReviews()).toEqual([]);
  expect(posts.map(post => new Headers(post.headers).get("Idempotency-Key"))).toEqual(["queue-fail:101", "queue-fail:101", "review-attempt:guided-review"]);
  await vi.advanceTimersByTimeAsync(0); // Settle cloned response-body bookkeeping.
  expect(vi.getTimerCount()).toBe(0);
});
