import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { retryBlockedOperation } from "../../app/lib/operation-status";
import { usePendingReviewRecovery } from "../../app/hooks/use-pending-review-recovery";
import { enqueuePendingReview, flushPendingReviews, pendingReviews } from "../../app/lib/review-outbox";
import { clearNotificationHistory, notificationToasts, notifications } from "../../app/lib/notifications";

beforeEach(() => { localStorage.clear(); clearNotificationHistory(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const review = { backendId: "card-one", queueEntryId: 101, attemptId: "original-attempt", outcome: "correct" as const, guided: false };
function Harness({ blocked = false, confirmed }: { blocked?: boolean; confirmed: () => void }) {
  usePendingReviewRecovery(true, true, blocked, confirmed); return null;
}
const advance = async (milliseconds: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); };

it("phone review recovery confirms the original receipt after reload without another POST", async () => {
  enqueuePendingReview(review);
  const fetcher = vi.fn().mockResolvedValue(Response.json({ state: "complete", response: { persisted: true, review_id: 77 } }));
  vi.stubGlobal("fetch", fetcher); const confirmed = vi.fn();
  render(<Harness confirmed={confirmed} />); await advance(1000);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toContain("/api/operations/review-attempt%3Aoriginal-attempt");
  expect(fetcher.mock.calls[0][1]?.method).not.toBe("POST");
  expect(pendingReviews()).toEqual([]); expect(confirmed).toHaveBeenCalledOnce();
});

it("phone pending receipt retries with bounded backoff and yields to foreground work", async () => {
  enqueuePendingReview(review);
  const fetcher = vi.fn(async () => Response.json({ state: "queued" }));
  vi.stubGlobal("fetch", fetcher); const confirmed = vi.fn();
  const mounted = render(<Harness confirmed={confirmed} />);
  await advance(1000); expect(fetcher).toHaveBeenCalledTimes(1);
  expect(pendingReviews()[0]).toMatchObject(review); expect(notificationToasts()).toEqual([]);
  expect(notifications()[0]).toMatchObject({ severity: "info", resolvedAt: null });
  await advance(999); expect(fetcher).toHaveBeenCalledTimes(1);
  await advance(1); expect(fetcher).toHaveBeenCalledTimes(2);
  await advance(1999); expect(fetcher).toHaveBeenCalledTimes(2);
  await advance(1); expect(fetcher).toHaveBeenCalledTimes(3);
  mounted.rerender(<Harness blocked confirmed={confirmed} />);
  await advance(30000); expect(fetcher).toHaveBeenCalledTimes(3);
  fetcher.mockResolvedValue(Response.json({ state: "complete", response: { persisted: true } }));
  mounted.rerender(<Harness confirmed={confirmed} />); await advance(1000);
  expect(pendingReviews()).toEqual([]); expect(confirmed).toHaveBeenCalledOnce();
  expect(notifications()[0].resolvedAt).not.toBeNull();
});

it("phone receipt recovery yields after one result and preserves another pending review", async () => {
  enqueuePendingReview(review); enqueuePendingReview({ ...review, attemptId: "second", queueEntryId: 102 });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "complete", response: { persisted: true } })));
  const confirmed = vi.fn(); render(<Harness confirmed={confirmed} />); await advance(1000);
  expect(fetch).toHaveBeenCalledOnce(); expect(pendingReviews().map(item => item.attemptId)).toEqual(["second"]);
  await advance(1000); expect(fetch).toHaveBeenCalledTimes(2); expect(pendingReviews()).toEqual([]);
});

it("phone blocked review receipt preserves the attempt and stops automatic retries", async () => {
  enqueuePendingReview(review);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ state: "blocked", message: "Restore database access" })));
  const confirmed = vi.fn(); render(<Harness confirmed={confirmed} />);
  await advance(1000); await advance(60000);
  expect(fetch).toHaveBeenCalledOnce(); expect(pendingReviews()[0]).toMatchObject(review);
  expect(confirmed).not.toHaveBeenCalled();
});

it("phone review receipt confirmation waits for held pointer release before refreshing training", async () => {
  enqueuePendingReview(review);
  let finishReceipt: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finishReceipt = resolve; })));
  const confirmed = vi.fn(); render(<Harness confirmed={confirmed} />);
  await advance(1000);
  window.dispatchEvent(new Event("pointerdown"));
  await act(async () => { finishReceipt!(Response.json({ state: "complete", response: { persisted: true } })); });
  expect(pendingReviews()).toEqual([]); expect(confirmed).not.toHaveBeenCalled();
  act(() => window.dispatchEvent(new Event("pointerup")));
  expect(confirmed).toHaveBeenCalledOnce();
});

it("phone recovery replays a missing receipt with the original immutable review identity", async () => {
  enqueuePendingReview(review);
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 404 }))
    .mockResolvedValueOnce(Response.json({ persisted: true }));
  vi.stubGlobal("fetch", fetcher); const confirmed = vi.fn();
  render(<Harness confirmed={confirmed} />); await advance(1000);
  expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "POST", headers: { "Idempotency-Key": "review-attempt:original-attempt" } });
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({ queue_entry_id: 101, outcome: "correct" });
  expect(pendingReviews()).toEqual([]); expect(confirmed).toHaveBeenCalledOnce();
});

it("phone receipt confirmed during a foreground pause refreshes once after resume", async () => {
  enqueuePendingReview(review);
  let finishReceipt: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finishReceipt = resolve; })));
  const confirmed = vi.fn(); const mounted = render(<Harness confirmed={confirmed} />);
  await advance(1000);
  mounted.rerender(<Harness blocked confirmed={confirmed} />);
  await act(async () => { finishReceipt!(Response.json({ state: "complete", response: { persisted: true } })); });
  expect(pendingReviews()).toEqual([]); expect(confirmed).not.toHaveBeenCalled();
  mounted.rerender(<Harness confirmed={confirmed} />);
  expect(confirmed).toHaveBeenCalledOnce();
  await advance(30000); expect(fetch).toHaveBeenCalledOnce();
});

it("phone readiness toggles retain one in-flight recovery and one confirmation callback", async () => {
  enqueuePendingReview(review);
  let finishReceipt: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finishReceipt = resolve; })));
  const confirmed = vi.fn(); const mounted = render(<Harness confirmed={confirmed} />);
  await advance(1000);
  mounted.rerender(<Harness blocked confirmed={confirmed} />);
  mounted.rerender(<Harness confirmed={confirmed} />);
  await advance(1000);
  await act(async () => { finishReceipt!(Response.json({ state: "complete", response: { persisted: true } })); });
  expect(fetch).toHaveBeenCalledOnce(); expect(confirmed).toHaveBeenCalledOnce();
  await advance(30000); expect(confirmed).toHaveBeenCalledOnce();
});

it("phone review storage failure suspends recovery and retains the confirmed result for repair", async () => {
  enqueuePendingReview(review);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Storage denied", "SecurityError"); });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "complete", response: { persisted: true } })));
  const confirmed = vi.fn(); render(<Harness confirmed={confirmed} />);
  await advance(1000); await advance(60000);
  expect(fetch).toHaveBeenCalledOnce(); expect(pendingReviews()[0]).toMatchObject(review);
  expect(confirmed).not.toHaveBeenCalled();
  expect(notifications().find(record => record.key === "review-save:original-attempt")).toMatchObject({
    severity: "warning", details: { classification: "storage", error: expect.stringContaining("Storage denied") } });
});


it.each([false, true])("phone definitive foreground failure suppresses idle replay across reload=%s", async reload => {
  enqueuePendingReview(review);
  const original = pendingReviews()[0];
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Invalid review" }, { status: 422 }))
    .mockImplementation(async (_url, options) => options?.method === "POST"
      ? Response.json({ persisted: true }) : new Response(null, { status: 404 }));
  vi.stubGlobal("fetch", fetcher);
  const mounted = render(<Harness confirmed={vi.fn()} />);
  await act(async () => { await expect(flushPendingReviews()).rejects.toMatchObject({ status: 422 }); });
  expect(pendingReviews()[0]).toMatchObject(original);
  if (reload) { mounted.unmount(); render(<Harness confirmed={vi.fn()} />); }
  await advance(60000);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(pendingReviews()[0]).toMatchObject(original);
});

it("phone blocked review suppression survives reload and permits explicit intervention", async () => {
  enqueuePendingReview(review);
  const original = pendingReviews()[0];
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ state: "blocked", message: "Repair service in Jobs" }));
  vi.stubGlobal("fetch", fetcher);
  const mounted = render(<Harness confirmed={vi.fn()} />);
  await advance(1000); mounted.unmount();
  render(<Harness confirmed={vi.fn()} />); await advance(60000);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(pendingReviews()[0]).toMatchObject(original);
  // Jobs can resolve the operation; Retry save then confirms that original receipt.
  fetcher.mockImplementation(async () => Response.json({ state: "complete", response: { persisted: true } }));
  await act(async () => { await retryBlockedOperation("review-attempt:original-attempt"); });
  expect(fetcher.mock.calls[1][0]).toContain("review-attempt%3Aoriginal-attempt/retry");
  expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "POST" });
  await advance(60000);
  expect(fetcher).toHaveBeenCalledTimes(3);
  await act(async () => { await flushPendingReviews(Infinity, true, original.attemptId); });
  expect(pendingReviews()).toEqual([]);
  expect(fetcher.mock.calls[3][0]).toContain("review-attempt%3Aoriginal-attempt");
});

it("phone terminal A does not starve independent B or allow a later same-card result", async () => {
  enqueuePendingReview(review);
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Invalid review" }, { status: 422 }))
    .mockResolvedValue(Response.json({ state: "complete", response: { persisted: true } }));
  vi.stubGlobal("fetch", fetcher);
  await expect(flushPendingReviews()).rejects.toMatchObject({ status: 422 });
  enqueuePendingReview({ ...review, attemptId: "later-same-card", queueEntryId: 102 });
  enqueuePendingReview({ ...review, backendId: "independent-card", attemptId: "independent", queueEntryId: 103 });
  render(<Harness confirmed={vi.fn()} />); await advance(60000);
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][0]).toContain("review-attempt%3Aindependent");
  expect(pendingReviews().map(item => item.attemptId)).toEqual(["original-attempt", "later-same-card"]);
});

it("phone explicitly retryable 409 retains bounded automatic recovery and immutable identity", async () => {
  enqueuePendingReview(review);
  const original = pendingReviews()[0];
  const posts: RequestInit[] = [];
  const fetcher = vi.fn(async (_url, options?: RequestInit) => {
    if (options?.method !== "POST") return new Response(null, { status: 404 });
    posts.push(options);
    return posts.length < 3 ? Response.json({ detail: "Busy", retryable: true }, { status: 409 })
      : Response.json({ persisted: true });
  });
  vi.stubGlobal("fetch", fetcher); render(<Harness confirmed={vi.fn()} />);
  await advance(1000); expect(posts).toHaveLength(1);
  await advance(999); expect(posts).toHaveLength(1);
  await advance(1); expect(posts).toHaveLength(2);
  await advance(1999); expect(posts).toHaveLength(2);
  await advance(1); expect(posts).toHaveLength(3);
  expect(posts.every(post => post.body === posts[0].body)).toBe(true);
  expect(posts.every(post => new Headers(post.headers).get("Idempotency-Key") === `review-attempt:${original.attemptId}`)).toBe(true);
  expect(pendingReviews()).toEqual([]);
});


it("phone transient recovery caps backoff at thirty seconds without changing the saved identity", async () => {
  enqueuePendingReview(review);
  const retained = localStorage.getItem("tempo-pending-training-reviews-v1");
  const fetcher = vi.fn(async () => Response.json({ detail: "Unavailable" }, { status: 503 }));
  vi.stubGlobal("fetch", fetcher); render(<Harness confirmed={vi.fn()} />);
  for (const [index, interval] of [1000, 1000, 2000, 4000, 8000, 16000, 30000, 30000].entries()) {
    await advance(interval - 1); expect(fetcher).toHaveBeenCalledTimes(index);
    await advance(1); expect(fetcher).toHaveBeenCalledTimes(index + 1);
    expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe(retained);
  }
  fetcher.mockResolvedValue(Response.json({ state: "complete", response: { persisted: true } }));
  await advance(30000); expect(pendingReviews()).toEqual([]);
});


it("phone terminal explicit retry cannot suspend an independent idle review sharing its in-flight flush", async () => {
  enqueuePendingReview({ ...review, automaticRecoverySuppressed: "failed" });
  enqueuePendingReview({ ...review, backendId: "independent-card", queueEntryId: 102, attemptId: "independent" });
  let rejectRetry: ((response: Response) => void) | undefined;
  const fetcher = vi.fn(async (url: RequestInfo | URL, options?: RequestInit) => {
    if (String(url).includes("review-attempt%3Aoriginal-attempt")) return new Response(null, { status: 404 });
    if (options?.method === "POST") return new Promise<Response>(resolve => { rejectRetry = resolve; });
    return Response.json({ state: "complete", response: { persisted: true } });
  });
  vi.stubGlobal("fetch", fetcher);
  const explicitRetry = flushPendingReviews(Infinity, true, "original-attempt");
  const rejected = expect(explicitRetry).rejects.toMatchObject({ status: 422 });
  render(<Harness confirmed={vi.fn()} />);
  await advance(1000); // B's idle observer shares A's still-running explicit flush.
  await act(async () => { rejectRetry!(Response.json({ detail: "Still invalid" }, { status: 422 })); await rejected; });
  await advance(60000);
  expect(pendingReviews().map(item => item.attemptId)).toEqual(["original-attempt"]);
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("review-attempt%3Aindependent"))).toHaveLength(1);
});


it("phone explicit retry after an idle terminal failure resumes automatic recovery when it becomes transient", async () => {
  enqueuePendingReview(review);
  let posts = 0;
  const fetcher = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => {
    if (options?.method !== "POST") return posts < 2 ? new Response(null, { status: 404 })
      : Response.json({ state: "complete", response: { persisted: true } });
    posts++;
    return Response.json({ detail: posts === 1 ? "Invalid" : "Busy" }, { status: posts === 1 ? 422 : 503 });
  });
  vi.stubGlobal("fetch", fetcher); render(<Harness confirmed={vi.fn()} />);
  await advance(1000);
  expect(pendingReviews()[0].automaticRecoverySuppressed).toBe("failed");
  await advance(60000); expect(posts).toBe(1);
  await act(async () => { await expect(flushPendingReviews(Infinity, true, review.attemptId))
    .rejects.toMatchObject({ classification: "transient" }); });
  expect(pendingReviews()[0].automaticRecoverySuppressed).toBeUndefined();
  await advance(60000);
  expect(pendingReviews()).toEqual([]);
  expect(posts).toBe(2); // The explicit replay lost confirmation; idle recovery only reads its receipt.
});
