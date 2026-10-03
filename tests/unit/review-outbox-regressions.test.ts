import { beforeEach, describe, expect, it, vi } from "vitest";
import { conflictedReviews, enqueuePendingReview, flushPendingReviews, pendingReviews, retryReviewConflict } from "../../app/lib/review-outbox";
import { enqueueTrainingFailure, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("optimistic training review outbox", () => {
  it("legacy identity storage failure attributes the retained result before any request", async () => {
    const original = JSON.stringify([{ backendId: "legacy-a", queueEntryId: 71, outcome: "correct", guided: false }]);
    localStorage.setItem("tempo-pending-training-reviews-v1", original);
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    try {
      await expect(flushPendingReviews()).rejects.toMatchObject({ backendId: "legacy-a", queueEntryId: 71, attemptId: "legacy-online:71", message: "Storage unavailable" });
      expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(original);
      expect(fetchMock).not.toHaveBeenCalled();
    } finally { write.mockRestore(); }
  });

  it("review timeout also bounds deferred operation receipt polling", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValueOnce(Response.json({ operation_id: "pending-a" }, { status: 202 }))
      .mockImplementationOnce((_input, options) => new Promise<Response>((_resolve, reject) => {
        options.signal.addEventListener("abort", () => reject(options.signal.reason));
      }));
    vi.stubGlobal("fetch", fetchMock);
    vi.useFakeTimers();
    try {
      const saving = flushPendingReviews();
      const rejected = expect(saving).rejects.toMatchObject({ queueEntryId: 17, message: expect.stringContaining("timed out") });
      await vi.advanceTimersByTimeAsync(15_000);
      await rejected;
      expect(pendingReviews()).toHaveLength(1);
      expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
    } finally { vi.useRealTimers(); }
  });
  it("legacy reload persists deterministic identity without inventing a completion time", async () => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "legacy-card", queueEntryId: 71, outcome: "correct", guided: false },
    ]));
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "historical rejection" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(flushPendingReviews()).rejects.toMatchObject({ status: 503, attemptId: "legacy-online:71" });
    expect(pendingReviews()[0]).toMatchObject({ attemptId: "legacy-online:71", state: "reconciling" });
    expect(pendingReviews()[0].completedAt).toBeUndefined();
    await flushPendingReviews();
    const bodies = fetchMock.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(bodies.every(body => body.attempt_id === "legacy-online:71" && body.recorded_at === undefined)).toBe(true);
    expect(fetchMock.mock.calls[1][1].headers).toEqual(fetchMock.mock.calls[2][1].headers);
  });

  it("explicit conflict retry changes transport identity while preserving completed evidence", async () => {
    const review = { backendId: "changed-card", queueEntryId: 72, outcome: "again" as const, guided: true,
      attemptId: "immutable-attempt", completedAt: "2026-09-30T12:00:00Z", expectedRevision: 3,
      state: "conflicted", reconciliationSequence: 1,
      conflict: { code: "card_revision_changed", message: "Card changed", retryable: false } };
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([review]));
    retryReviewConflict(review.attemptId);
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("review-reconcile:immutable-attempt:2");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      outcome: "again", guided: true, queue_entry_id: 72, attempt_id: review.attemptId,
      recorded_at: review.completedAt, expected_revision: 3,
    });
  });

  it("same-card dependent result waits while an unrelated card persists", async () => {
    localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([
      { backendId: "card-a", queueEntryId: 70, outcome: "correct", guided: false, attemptId: "older-a", state: "conflicted",
        conflict: { code: "card_revision_changed", message: "Card changed", retryable: false } },
    ]));
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 71, outcome: "again", guided: false });
    enqueuePendingReview({ backendId: "card-b", queueEntryId: 72, outcome: "correct", guided: false });
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("card-b/review");
    expect(conflictedReviews()).toHaveLength(2);
    expect(conflictedReviews()[1].conflict?.code).toBe("earlier_review_conflict");
  });

  it("failed atomic conflict write preserves A and pauses B without data loss", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "stale" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "No proof", retryable: false } }));
    vi.stubGlobal("fetch", fetchMock);
    const originalWrite = Storage.prototype.setItem;
    const write = vi.spyOn(Storage.prototype, "setItem");
    write.mockImplementation(function (this: Storage, key, value) {
      if (key === "tempo-pending-training-reviews-v1" && value.includes('"state":"conflicted"'))
        throw new DOMException("Storage full", "QuotaExceededError");
      return originalWrite.call(this, key, value);
    });
    try {
      await expect(flushPendingReviews()).rejects.toMatchObject({ queueEntryId: 17, message: expect.stringContaining("Storage full") });
      expect(pendingReviews().map(review => review.queueEntryId)).toEqual([17, 18]);
      expect(conflictedReviews()).toEqual([]);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    } finally { write.mockRestore(); }
  });

  it("deferred failed receipt retains conflict classification and reconciles the same attempt", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    const attemptId = pendingReviews()[0].attemptId;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ operation_id: "old-save" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ state: "failed", error: { status_code: 409, code: "queue_attempt_retired", retryable: false, detail: "Retired attempt" } }))
      .mockResolvedValueOnce(Response.json({ persisted: true }));
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(String(fetchMock.mock.calls[2][0])).toContain("/review/reconcile");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body).attempt_id).toBe(attemptId);
    expect(pendingReviews()).toEqual([]);
  });
  it("terminal stale A remains inspectable while independent B and C persist", async () => {
    for (const [backendId, queueEntryId] of [["card-a", 17], ["card-b", 18], ["card-c", 19]] as const)
      enqueuePendingReview({ backendId, queueEntryId, outcome: "correct", guided: false });
    const initialAttempts = pendingReviews();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const endpoint = String(input);
      if (endpoint.endsWith("/card-a/review"))
        return Response.json({ detail: "This queue attempt is no longer available", code: "queue_attempt_unavailable", retryable: false }, { status: 409 });
      if (endpoint.endsWith("/card-a/review/reconcile"))
        return Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "The original queue context is unavailable.", retryable: false } });
      return Response.json({ persisted: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    const retained = JSON.parse(localStorage.getItem("tempo-pending-training-reviews-v1")!);
    expect(retained).toEqual([expect.objectContaining({ ...initialAttempts[0], state: "conflicted" })]);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      expect.stringContaining("/card-a/review"), expect.stringContaining("/card-a/review/reconcile"),
      expect.stringContaining("/card-b/review"), expect.stringContaining("/card-c/review"),
    ]);
  });

  it("replay rejection identifies old A rather than the current B", async () => {
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17, outcome: "correct", guided: false });
    enqueuePendingReview({ backendId: "card-b", queueEntryId: 18, outcome: "correct", guided: false });
    const attemptId = pendingReviews()[0].attemptId;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "busy" }, { status: 503 })));
    await expect(flushPendingReviews()).rejects.toMatchObject({
      backendId: "card-a", queueEntryId: 17, attemptId, status: 503, retryable: true,
    });
  });
  it("confirmed guided review clears its earlier failure marker", async () => {
    enqueueTrainingFailure(17);
    enqueuePendingReview({ backendId: "card-a", queueEntryId: 17,
      outcome: "again", guided: true });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ persisted: true })));
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(pendingTrainingFailures()).toEqual([]);
  });
  it("retains a failed review for retry after reload without duplicating the queue entry", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: false };
    enqueuePendingReview(review);
    enqueuePendingReview(review);
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ detail: "busy" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true })));
    await expect(flushPendingReviews()).rejects.toThrow("busy");
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
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

  it("unprovable guided result becomes a durable nonblocking conflict", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: true };
    enqueuePendingReview(review);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "Original attempt unavailable", retryable: false } })));

    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(conflictedReviews()).toEqual([expect.objectContaining({ ...review, state: "conflicted" })]);
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
    expect(flushPendingReviews()).toBe(saving);
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
      expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
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
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toMatch(/^review-attempt:/);
    expect(fetchMock.mock.calls[2][1].headers["Idempotency-Key"]).toBe(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]);
  });

  it("retries one phone review identity after an uncertain save and clears it only on confirmation", async () => {
    enqueuePendingReview({ backendId: "phone-card", queueEntryId: 91, outcome: "correct", guided: false });
    const saved = pendingReviews()[0];
    expect(saved.attemptId).toBeTruthy();
    expect(saved.completedAt).toBeTruthy();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "Temporarily unavailable" }, { status: 503 }))
      .mockResolvedValueOnce(Response.json({ persisted: true, review_id: 32,
        reconciliation: "chronological", warning: null }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(flushPendingReviews()).rejects.toThrow("Temporarily unavailable");
    expect(pendingReviews()[0]).toEqual(saved);
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({
      attempt_id: saved.attemptId, recorded_at: saved.completedAt,
    });
  });

  it("keeps a phone review when the server omits persistence confirmation", async () => {
    enqueuePendingReview({ backendId: "phone-card", queueEntryId: 92, outcome: "again", guided: false });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
    await expect(flushPendingReviews()).rejects.toThrow("did not confirm");
    expect(pendingReviews()).toHaveLength(1);
  });
});
