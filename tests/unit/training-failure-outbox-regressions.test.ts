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
  enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 41,
    outcome: "correct", guided: false });
  enqueueTrainingFailure(42);
  const fetcher = vi.fn(async (url: string) => String(url).endsWith("/review")
    ? Response.json({ persisted: true }) : Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", fetcher);
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
  enqueueTrainingFailure(42);
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
  enqueueTrainingFailure(1974);
  enqueueTrainingFailure(1974);
  expect(pendingTrainingFailures()).toEqual([1974]);
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", fetcher);
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
  enqueueTrainingFailure(1974);
  const fetcher = vi.fn(async (url: string) => String(url).endsWith("/state")
    ? Response.json({ state: "completed", attempt_failed: true })
    : Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await flushTrainingFailures();
  expect(pendingTrainingFailures()).toEqual([]);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    expect.stringContaining("/api/queue/entries/1974/fail"),
    expect.stringContaining("/api/queue/entries/1974/state"),
  ]);
});

it("stale failed-attempt marker cannot fail a different queued entry", async () => {
  enqueueTrainingFailure(1974);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => String(url).endsWith("/state")
    ? Response.json({ state: "unavailable", attempt_failed: false })
    : Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 })));
  await expect(flushTrainingFailures()).rejects.toThrow("no longer available");
  expect(pendingTrainingFailures()).toEqual([1974]);
});


it("guided failure replay preserves its displayed card revision through a transient retry", async () => {
  enqueueTrainingFailure(42, "original-card", 3);
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ attempt_failed: true }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTrainingFailures()).rejects.toThrow("busy");
  await flushTrainingFailures();
  expect(fetcher.mock.calls.map(([, request]) => JSON.parse(request.body))).toEqual([
    { card_id: "original-card", expected_revision: 3 }, { card_id: "original-card", expected_revision: 3 },
  ]);
  expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBe(fetcher.mock.calls[1][1].headers["Idempotency-Key"]);
});

it("explicit ambiguous guided marker conflict cannot rotate onto replacement content", async () => {
  enqueueTrainingFailure(42, "original-card", 3);
  const fetcher = vi.fn().mockResolvedValue(Response.json({ code: "queue_attempt_unprovable", detail: "Original attempt changed" }, { status: 409 }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushTrainingFailures()).rejects.toThrow("Original attempt changed");
  await flushTrainingFailures();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(pendingTrainingFailures()).toEqual([]);
});


it("old completed review cannot clear a replacement card's guided marker on the same queue ID", () => {
  enqueueTrainingFailure(42, "original-card", 1);
  enqueueTrainingFailure(42, "replacement-card", 1);
  clearTrainingFailureAfterReview(42, "original-card", 1);
  expect(pendingTrainingFailureContexts()).toEqual([
    expect.objectContaining({ queueEntryId: 42, backendId: "replacement-card", expectedRevision: 1 }),
  ]);
});


it.each([
  { name: "numeric", saved: [42] },
  { name: "object", saved: [{ queueEntryId: 42, operationId: "legacy-failure-key" }] },
])("legacy $name failure replay preserves its key and missing context while independent reviews save", async ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 41, outcome: "correct", guided: false });
  enqueuePendingReview({ backendId: "later-card", queueEntryId: 43, outcome: "correct", guided: false });
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
  vi.stubGlobal("fetch", fetcher);
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
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/fail")
    ? new Promise<Response>(resolve => { releaseMarker = resolve; })
    : Response.json({ persisted: true })));
  const heldMarkerDelivery = flushTrainingFailures();
  const heldMarkerFailure = expect(heldMarkerDelivery).rejects.toThrow("Temporarily unavailable");
  try {
    expect(releaseMarker).toBeDefined();
    enqueuePendingReview({ backendId: "independent-later-card", queueEntryId: 44, outcome: "correct", guided: false });
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
])("legacy $name marker cannot be cleared by a review of replacement content sharing its queue ID", ({ saved }) => {
  localStorage.setItem("tempo-pending-training-failures-v1", JSON.stringify(saved));
  const normalized = pendingTrainingFailureContexts();
  clearTrainingFailureAfterReview(42, "replacement-card", 2);
  expect(pendingTrainingFailureContexts()).toEqual(normalized);
});
