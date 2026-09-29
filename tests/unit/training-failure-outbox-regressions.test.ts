import { beforeEach, expect, it, vi } from "vitest";
import {
  enqueueTrainingFailure,
  flushTrainingFailures,
  pendingTrainingFailures,
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
  expect(pendingTrainingFailures()).toEqual([]);
});
