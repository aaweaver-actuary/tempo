import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePendingReviewRecovery } from "../../app/hooks/use-pending-review-recovery";
import { enqueuePendingReview, pendingReviews } from "../../app/lib/review-outbox";
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
