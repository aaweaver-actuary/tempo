import { withMissingReceipts } from "./support/study-intent-fixtures";
import { initializeStudyIntentStorage } from "../../database/browser/study-intent-storage";
import { beforeEach, expect, it, vi } from "vitest";
import {
  enqueueTrainingFailure,
  flushTrainingFailures,
  pendingTrainingFailures,
  pendingTrainingFailureContexts,
  clearTrainingFailureAfterReview,
} from "../../app/lib/training-failure-outbox";
import { enqueuePendingReview, flushPendingReviews } from "../../app/lib/review-outbox";

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });

it("prefetched guided failure waits for the prior review and survives reload", async () => {
  await enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 41,
    outcome: "correct", guided: false });
  await enqueueTrainingFailure(42);
  const fetcher = vi.fn(async (url: string) => String(url).endsWith("/review")
    ? Response.json({ persisted: true }) : Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await flushTrainingFailures();
  expect(fetcher).not.toHaveBeenCalled();
  expect(pendingTrainingFailures()).toEqual([42]);
  await flushPendingReviews();
  await flushTrainingFailures();
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/api/cards/earlier-card/review"),
    expect.stringContaining("/api/queue/entries/42/fail"),
  ]);
  expect(pendingTrainingFailures()).toEqual([]);
});

it("queued guided failure 409 retains its marker and rotates a poisoned operation key", async () => {
  await enqueueTrainingFailure(42);
  const firstPostKeys: string[] = [];
  let requestCount = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (String(url).endsWith("/state"))
      return Response.json({ state: requestCount === 1 ? "queued" : "head", attempt_failed: false });
    requestCount += 1;
    firstPostKeys.push(new Headers(options?.headers).get("Idempotency-Key") ?? "");
    return requestCount === 1
      ? Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 })
      : Response.json({ attempt_failed: true });
  }));
  await flushTrainingFailures();
  expect(pendingTrainingFailures()).toEqual([42]);
  await flushTrainingFailures();
  expect(firstPostKeys).toHaveLength(2);
  expect(firstPostKeys[0]).not.toBe(firstPostKeys[1]);
  expect(pendingTrainingFailures()).toEqual([]);
});

it("intentional failed attempt survives reload and replays once for its queue entry", async () => {
  await enqueueTrainingFailure(1974);
  await enqueueTrainingFailure(1974);
  expect(pendingTrainingFailures()).toEqual([1974]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await expect(flushTrainingFailures()).rejects.toThrow("busy");
  expect(pendingTrainingFailures()).toEqual([1974]);
  await flushTrainingFailures();
  expect(pendingTrainingFailures()).toEqual([]);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(
    fetcher.mock.calls[1][1].headers["Idempotency-Key"]);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/api/queue/entries/1974/fail"),
    expect.stringContaining("/api/queue/entries/1974/fail"),
  ]);
});

it("completed queue entry clears a stale guided marker without another failure", async () => {
  await enqueueTrainingFailure(1974);
  const fetcher = vi.fn(async (url: string) => String(url).endsWith("/state")
    ? Response.json({ state: "completed", attempt_failed: true })
    : Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await flushTrainingFailures();
  expect(pendingTrainingFailures()).toEqual([]);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/api/queue/entries/1974/fail"),
    expect.stringContaining("/api/queue/entries/1974/state"),
  ]);
});

it("stale failed-attempt marker cannot fail a different queued entry", async () => {
  await enqueueTrainingFailure(1974);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).endsWith("/state")
    ? Response.json({ state: "unavailable", attempt_failed: false })
    : Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 })));
  await expect(flushTrainingFailures()).rejects.toThrow("no longer available");
  expect(pendingTrainingFailures()).toEqual([1974]);
});


it("guided failure replay preserves its displayed card revision through a transient retry", async () => {
  await enqueueTrainingFailure(42, "original-card", 3);
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await expect(flushTrainingFailures()).rejects.toThrow("busy");
  await flushTrainingFailures();
  expect(fetcher.mock.calls.map(([, request]) => JSON.parse(request.body))).toEqual([
    { card_id: "original-card", expected_revision: 3 }, { card_id: "original-card", expected_revision: 3 },
  ]);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(fetcher.mock.calls[1][1].headers["Idempotency-Key"]);
});

it("explicit ambiguous guided marker conflict cannot rotate onto replacement content", async () => {
  await enqueueTrainingFailure(42, "original-card", 3);
  const fetcher = vi.fn().mockResolvedValue(Response.json({ code: "queue_attempt_unprovable", detail: "Original attempt changed" }, { status: 409 }));
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await expect(flushTrainingFailures()).rejects.toThrow("Original attempt changed");
  await flushTrainingFailures();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(pendingTrainingFailures()).toEqual([]);
});


it("old completed review cannot clear a replacement card's guided marker on the same queue ID", async () => {
  await enqueueTrainingFailure(42, "original-card", 1);
  await enqueueTrainingFailure(42, "replacement-card", 1);
  await clearTrainingFailureAfterReview(42, "original-card", 1);
  expect(pendingTrainingFailureContexts()).toEqual([
    expect.objectContaining({ queueEntryId: 42, backendId: "replacement-card", expectedRevision: 1 }),
  ]);
});


it.each([
  { name: "numeric", saved: [42] },
  { name: "object", saved: [{ queueEntryId: 42, operationId: "legacy-failure-key" }] },
])("legacy $name failure replay preserves its key and missing context while independent reviews save", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  await initializeStudyIntentStorage(true);
  await enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 41, outcome: "correct", guided: false });
  await enqueuePendingReview({ backendId: "later-card", queueEntryId: 43, outcome: "correct", guided: false });
  const reviewIds: number[] = [];
  const keys: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    if (String(input).endsWith("/review")) {
      reviewIds.push(JSON.parse(String(options?.body)).queue_entry_id);
      return Response.json({ persisted: true });
    }
    keys.push(new Headers(options?.headers).get("Idempotency-Key")!);
    expect(options?.body).toBeUndefined();
    return Response.json({ detail: "Temporarily unavailable" }, { status: 503 });
  });
  vi.stubGlobal("fetch", withMissingReceipts(fetcher));
  await flushTrainingFailures();
  expect(fetcher).not.toHaveBeenCalled();
  const normalized = pendingTrainingFailureContexts();
  await flushPendingReviews();
  expect(reviewIds).toEqual([41, 43]);
  await expect(flushTrainingFailures()).rejects.toThrow("Temporarily unavailable");
  expect(pendingTrainingFailureContexts()).toEqual(normalized);
  await expect(flushTrainingFailures()).rejects.toThrow("Temporarily unavailable");
  expect(keys).toEqual([normalized[0].operationId, normalized[0].operationId]);
  expect(normalized).toEqual([{ queueEntryId: 42, operationId: expect.any(String) }]);
  let releaseMarker!: (response: Response) => void;
  vi.stubGlobal("fetch", withMissingReceipts(vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? new Promise<Response>(resolve => { releaseMarker = resolve; })
    : Response.json({ persisted: true }))));
  const heldMarkerDelivery = flushTrainingFailures();
  const heldMarkerFailure = expect(heldMarkerDelivery).rejects.toThrow("Temporarily unavailable");
  try {
    await vi.waitFor(() => expect(releaseMarker).toBeTypeOf("function"));
    await enqueuePendingReview({ backendId: "independent-later-card", queueEntryId: 44, outcome: "correct", guided: false });
    expect((await flushPendingReviews()).persistedAttemptIds).toHaveLength(1);
    expect(pendingTrainingFailureContexts()).toEqual(normalized);
  } finally {
    releaseMarker(Response.json({ detail: "Temporarily unavailable" }, { status: 503 }));
    await heldMarkerFailure;
  }
});

it.each([
  { name: "numeric", saved: [42] },
  { name: "object", saved: [{ queueEntryId: 42, operationId: "legacy-review-clear-key" }] },
])("legacy $name marker cannot be cleared by a review of replacement content sharing its queue ID", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  await initializeStudyIntentStorage(true);
  const normalized = pendingTrainingFailureContexts();
  await clearTrainingFailureAfterReview(42, "replacement-card", 2);
  expect(pendingTrainingFailureContexts()).toEqual(normalized);
});

it.each(["missing", "foreign"])("%s guided failure acknowledgment retains its frozen identity and payload", async kind => {
  await enqueueTrainingFailure(1999, "original-guided", 3);
  const original = pendingTrainingFailureContexts()[0];
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push(String(input));
    if (init?.method !== "POST") return Response.json({ state: "unknown" });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(original.operationId);
    expect(init.body).toBe(JSON.stringify({ card_id: "original-guided", expected_revision: 3 }));
    return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
  }));
  await expect(flushTrainingFailures()).rejects.toMatchObject({ name: "PendingOperationError", operationId: original.operationId });
  expect(pendingTrainingFailureContexts()).toEqual([original]);
  expect(calls).toHaveLength(1); // initial POST only
  expect(calls.some(url => url.includes("foreign"))).toBe(false);
});

it('FE25 bound failure cleanup preserves the original marker if authority changes before its update', async () => {
  await enqueueTrainingFailure(42, 'original-card', 1);
  const { studyIntentStore } = await import('../../database/browser/study-intent-storage'); const store = await studyIntentStore();
  const before = await store.list('failure'); let current = true;
  const original = store.update.bind(store);
  const update = vi.spyOn(store, 'update').mockImplementation(async (key, change) => original(key, record => { current = false; return change(record); }));
  try {
    await expect(clearTrainingFailureAfterReview(42, 'original-card', 1, { store, requireCurrent: () => { if (!current) throw new Error('authority changed'); } })).rejects.toThrow('authority changed');
    expect(await store.list('failure')).toEqual(before);
  } finally { update.mockRestore(); }
});
