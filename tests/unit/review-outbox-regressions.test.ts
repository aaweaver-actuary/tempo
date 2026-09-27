import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueuePendingReview, flushPendingReviews, pendingReviews } from "../../app/lib/review-outbox";

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("optimistic training review outbox", () => {
  it("retains a failed review for retry after reload without duplicating the queue entry", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: false };
    enqueuePendingReview(review);
    enqueuePendingReview(review);
    expect(pendingReviews()).toEqual([review]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true })));
    await expect(flushPendingReviews()).rejects.toThrow("busy");
    expect(pendingReviews()).toEqual([review]);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
  });

  it("replays failed-attempt marking before grading and preserves order", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "again", guided: true });
    enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/api/queue/entries/17/fail"),
      expect.stringContaining("/api/cards/card-a/review"),
      expect.stringContaining("/api/cards/card-b/review"),
    ]);
    expect(pendingReviews()).toEqual([]);
  });

  it("stale guided failure marking still saves the guided review once", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: true });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);

    await flushPendingReviews();

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/api/queue/entries/17/fail"),
      expect.stringContaining("/api/cards/card-a/review"),
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ guided: true, queue_entry_id: 17 });
    expect(pendingReviews()).toEqual([]);
  });

  it("unresolved guided review remains saved when the review endpoint rejects it", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: true };
    enqueuePendingReview(review);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 })));

    await expect(flushPendingReviews()).rejects.toThrow("no longer available");
    expect(pendingReviews()).toEqual([review]);
  });

  it("drains a review appended while an earlier review request is still in flight", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    let finishFirst: ((response: Response) => void) | undefined;
    const savedEntries: number[] = [];
    vi.stubGlobal("fetch", vi.fn((input) => {
      const queueEntryId = String(input).includes("card-a") ? 17 : 18;
      savedEntries.push(queueEntryId);
      return queueEntryId === 17
        ? new Promise<Response>((resolve) => { finishFirst = resolve; })
        : Promise.resolve(Response.json({ persisted: true }));
    }));
    const saving = flushPendingReviews();
    enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    finishFirst?.(Response.json({ persisted: true }));
    await saving;
    expect(savedEntries).toEqual([17, 18]);
    expect(pendingReviews()).toEqual([]);
  });

  it("times out an unresponsive review save and keeps it available for retry", async () => {
    const review = { backendId: "card-stalled", queueEntryId: 19, outcome: "correct" as const, guided: false };
    enqueuePendingReview(review);
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_input, options) => new Promise<Response>((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason));
    })));
    try {
      const saving = flushPendingReviews();
      const rejected = expect(saving).rejects.toThrow(/timed out/i);
      await vi.advanceTimersByTimeAsync(15_000);
      await rejected;
      expect(pendingReviews()).toEqual([review]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps a Celery-accepted review until its operation receipt confirms the save", async () => {
    const review = { backendId: "card-pending", queueEntryId: 81, outcome: "correct" as const, guided: false };
    enqueuePendingReview(review);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "pending" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "pending" }))
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "pending" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ operation_id: "review:81", state: "complete", response: { persisted: true } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(flushPendingReviews()).rejects.toThrow("still pending");
    expect(pendingReviews()).toEqual([review]);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("review:81");
    expect(fetchMock.mock.calls[2][1].headers["Idempotency-Key"]).toBe("review:81");
  });
});
