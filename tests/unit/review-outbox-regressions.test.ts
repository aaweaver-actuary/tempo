import { beforeEach, describe, expect, it, vi } from "vitest";
import { enqueuePendingReview, flushPendingReviews, pendingReviews } from "../../app/lib/review-outbox";
import { enqueueTrainingFailure, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
import manifest from "../fixtures/opening-evidence-manifest.json";
import { openingEvidenceCheckpointSchema } from "../../app/domain/opening-evidence";

beforeEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("optimistic training review outbox", () => {
  it.each([
    ["immediate", "opening_evidence_conflict"], ["immediate", "opening_evidence_unavailable"],
    ["deferred", "opening_evidence_conflict"], ["deferred", "opening_evidence_unavailable"],
  ] as const)("AS-16 %s %s archives rejected journal before confirmed aggregate-only save", async (timing, code) => {
    vi.stubGlobal("indexedDB", undefined);
    const completion=openingEvidenceCheckpointSchema.parse({ attempt_id:"rejected-journal", manifest,
      origin_queue_entry_id:101,queue_entry_id:101,started_at:"2026-09-30T12:00:00Z",study_timezone:"UTC",
      events:[{sequence:1,decision_index:0,decision_id:manifest.decisions[0].decision_id,
        expected_uci:"e2e4",kind:"first_response",response_uci:"e2e4",observed_at:"2026-09-30T12:00:01Z",disposition:"expected"}],
      terminal:{state:"complete",final_sequence:1,ended_at:"2026-09-30T12:01:00Z"} });
    enqueuePendingReview({backendId:"shadow-card",queueEntryId:101,outcome:"correct",guided:false,
      attemptId:completion.attempt_id,openingEvidenceCompletion:completion});
    const original=pendingReviews()[0];
    const detail={code,message:"Rejected immutable evidence",aggregate_review_allowed:true};
    const storedMessage=`409: {'code': '${code}', 'message': 'Rejected immutable evidence', 'aggregate_review_allowed': True}`;
    const rejection=timing==="immediate"?detail.message:storedMessage;
    const posts: RequestInit[]=[];
    vi.stubGlobal("fetch",vi.fn(async (_url,options?:RequestInit) => {
      if (options?.method!=="POST") return Response.json({state:"failed",error:{status_code:409,message:storedMessage,detail}});
      posts.push(options);
      if (posts.length===1) return timing==="immediate"?Response.json({detail},{status:409})
        :Response.json({operation_id:"review-attempt:rejected-journal",state:"pending"},{status:202});
      expect(JSON.parse(localStorage.getItem("tempo-rejected-opening-reviews-v1") ?? "[]")).toEqual([
        {...original,evidenceRejected:rejection},
      ]);
      return Response.json({persisted:true});
    }));
    await flushPendingReviews();
    expect(pendingReviews()).toEqual([]);
    expect(JSON.parse(localStorage.getItem("tempo-rejected-opening-reviews-v1") ?? "[]")).toEqual([
      {...original,evidenceRejected:rejection},
    ]);
    expect(posts).toHaveLength(2);
    const firstBody=JSON.parse(posts[0].body as string);
    expect(firstBody.opening_evidence_completion).toEqual(completion);
    const aggregateBody={...firstBody};delete aggregateBody.opening_evidence_completion;
    expect(JSON.parse(posts[1].body as string)).toEqual(aggregateBody);
    const firstKey=new Headers(posts[0].headers).get("Idempotency-Key");
    expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe(`${firstKey}:aggregate-only`);
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

  it("unresolved guided review remains saved when the review endpoint rejects it", async () => {
    const review = { backendId: "card-a", queueEntryId: 17, outcome: "correct" as const, guided: true };
    enqueuePendingReview(review);
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 })));

    await expect(flushPendingReviews()).rejects.toThrow("no longer available");
    expect(pendingReviews()).toEqual([expect.objectContaining(review)]);
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
