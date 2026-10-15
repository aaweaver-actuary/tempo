import { advanceStudyTimers, settleStudyStorageTasks } from "./support/study-intent-fixtures";
import { initializeStudyIntentStorage } from "../../app/lib/study-intent-storage";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import Home from "../../app/views/home_view";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { conflictedReviews, enqueuePendingReview, flushPendingReviews, pendingReviews, type PendingReview } from "../../app/lib/review-outbox";
import { clearNotificationHistory, notifications } from "../../app/lib/notifications";
import { clearDebugErrors, debugErrors, buildDebugBundle } from "../../app/lib/debug-reporting";
import { asFenString } from "../../app/types";
import { mapQueueCardToPracticeCard } from "../../app/domain/adapters/practice-card-adapters";
import { runStudyTask } from "../../app/lib/background-study";

let trainingProps: React.ComponentProps<typeof TrainingView>;
vi.mock("../../app/views/training_view", async importOriginal => {
  const { default: ActualTrainingView } = await importOriginal<typeof import("../../app/views/training_view")>();
  return { default: (props: React.ComponentProps<typeof ActualTrainingView>) => {
    trainingProps = props;
    return <ActualTrainingView {...props} />;
  } };
});
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
// This separate pipeline is unchanged; isolate review recovery's idle opportunities.
vi.mock("../../app/hooks/use-opening-evidence-recovery", () => ({ useOpeningEvidenceRecovery: vi.fn() }));
vi.mock("../../app/lib/background-study", async () => {
  const { createStudyPositionStore } = await import("../../app/lib/study-position-store");
  const compute = createStudyPositionStore();
  return { runStudyTask: vi.fn(async task => compute(task)) };
});

const queueCard = (id: string, entryId: number) => ({ id, queue_entry_id: entryId,
  start_fen: new Chess().fen(), moves: ["e2e4", "e7e5", "g1f3"], content_type: "opening",
  repertoire_name: id, repertoire_source: "PGN", revision: 1 });
let queue: ReturnType<typeof queueCard>[];
let idleCallbacks: Map<number, IdleRequestCallback>;
let reviewResponse: (input: string, options?: RequestInit) => Promise<Response>;
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
const suppressedReview: PendingReview = { backendId: "card-a", queueEntryId: 11, attemptId: "earlier-a",
  completedAt: "2026-10-08T12:00:00Z", outcome: "again", guided: false, automaticRecoverySuppressed: "failed" };

beforeEach(() => {
  clearNotificationHistory();
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  queue = [queueCard("card-a", 12)];
  idleCallbacks = new Map();
  let idleSequence = 0;
  vi.stubGlobal("requestIdleCallback", vi.fn((callback: IdleRequestCallback) => {
    idleCallbacks.set(++idleSequence, callback); return idleSequence;
  }));
  vi.stubGlobal("cancelIdleCallback", vi.fn((identity: number) => idleCallbacks.delete(identity)));
  reviewResponse = async () => Response.json({ persisted: true });
  fetcher = vi.fn<typeof fetch>(async (input, options) => {
    const url = String(input);
    if (url.includes("/api/queue/window")) return Response.json({ cards: queue, count: queue.length, local_date: new Date().toLocaleDateString("en-CA") });
    if (url.includes("/api/operations/") || url.endsWith("/review") || url.endsWith("/review/reconcile"))
      return reviewResponse(url, options);
    return Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] });
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each(["headers", "body", "worker", "abort"])("issue186_post_review_acquisition_has_bounded_recovery: %s", async failure => {
  await readyHome();
  const originalFetcher = fetcher.getMockImplementation()!;
  const originalCompute = vi.mocked(runStudyTask).getMockImplementation()!;
  let acquisitionCount = 0;
  fetcher.mockImplementation(async (input, options) => {
    if (!String(input).includes("/api/queue/window")) return originalFetcher(input, options);
    acquisitionCount++;
    if (failure === "headers") return new Promise((_resolve, reject) => options?.signal?.addEventListener("abort",
      () => reject(new DOMException("Fetch is aborted", "AbortError")), { once: true }));
    if (failure === "abort") throw new DOMException("Fetch is aborted", "AbortError");
    const response = Response.json({ cards: [queueCard("card-b", 13)], count: 1 });
    if (failure === "body") vi.spyOn(response, "json").mockImplementation(() => new Promise(() => {}));
    return response;
  });
  if (failure === "worker") vi.mocked(runStudyTask).mockImplementation(task => task.kind === "queue"
    ? new Promise(() => {}) : originalCompute(task));
  try {
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    await act(async () => { await advanceStudyTimers(15_001); await advanceStudyTimers(15_001); });
    expect(trainingProps.reviewPersistenceState).toBe("queueFailed");
    expect(acquisitionCount).toBe(2);
    expect(screen.getByRole("button", { name: "Retry loading the queue" })).toBeTruthy();
    expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false });
    expect(reviewPosts()).toHaveLength(1);
    expect(pendingReviews()).toEqual([]);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  } finally { vi.mocked(runStudyTask).mockImplementation(originalCompute); }
});

it("issue186_healthy_fallback_does_not_wait_for_integrity", async () => {
  await readyHome();
  queue = [queueCard("card-b", 13)];
  const originalFetcher = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (input, options) => String(input).endsWith("/api/repertoires")
    ? new Promise(() => {}) : originalFetcher(input, options));
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  await act(async () => { await advanceStudyTimers(0); });
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
  expect(trainingProps.reviewPersistenceState).toBe("idle");
  expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false });
  expect(reviewPosts()).toHaveLength(1);
});

it("issue186_queue_retry_recovers_without_resubmitting_saved_review", async () => {
  await readyHome();
  const originalFetcher = fetcher.getMockImplementation()!;
  let queueUnavailable = true;
  fetcher.mockImplementation(async (input, options) => {
    if (String(input).includes("/api/queue/window") && queueUnavailable) throw new DOMException("Fetch is aborted", "AbortError");
    return originalFetcher(input, options);
  });
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(trainingProps.reviewPersistenceState).toBe("queueFailed");
  queueUnavailable = false;
  queue = [queueCard("card-b", 13)];
  await act(async () => { trainingProps.retryQueueAfterReview!(); await settleStudyStorageTasks(); });
  expect(trainingProps.reviewPersistenceState).toBe("idle");
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
  expect(reviewPosts()).toHaveLength(1);
  expect(notifications().filter(record => record.key === "review-queue:12")).toHaveLength(1);
  expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false, severity: "success" });
});

it("issue186_authoritative_empty_queue_settles_saved_progression", async () => {
  await readyHome();
  queue = [];
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(trainingProps.reviewPersistenceState).toBe("idle");
  expect(useTrainingStore.getState().cardsLeft).toBe(0);
  expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false,
    message: "Result saved. Training queue complete." });
});

it("issue186_ordinary_reconnection_replays_saved_reviews_without_replacing_active_attempt", async () => {
  await readyHome();
  const activeAttempt = useTrainingStore.getState().attempt;
  reviewResponse = async url => url.includes("/api/operations/")
    ? new Response(null, { status: 404 }) : Response.json({ persisted: true });
  await enqueuePendingReview({ backendId: "earlier-card", queueEntryId: 11,
    attemptId: "earlier-offline-result", outcome: "correct", guided: false });
  await act(async () => { window.dispatchEvent(new Event("online")); });
  await act(async () => { await advanceStudyTimers(0); });
  expect(reviewPosts()).toHaveLength(1);
  expect(String(reviewPosts()[0][0])).toContain("/api/cards/earlier-card/review");
  expect(pendingReviews()).toEqual([]);
  expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
});

it.each(["fallback", "prepared"])("issue186_periodic_refresh_preserves_progression_owner: %s", async path => {
  const refreshIntervals: (() => void)[] = [];
  const originalInterval = window.setInterval.bind(window);
  const intervalSpy = vi.spyOn(window, "setInterval").mockImplementation((handler, delay, ...arguments_) => {
    if (delay === 30_000 && typeof handler === "function") refreshIntervals.push(() => handler(...arguments_));
    return originalInterval(handler, delay, ...arguments_) as unknown as ReturnType<typeof window.setInterval>;
  });
  try {
    if (path === "prepared") queue = [queueCard("card-a", 12), queueCard("card-b", 13)];
    await readyHome();
    expect(refreshIntervals.length).toBeGreaterThan(0);
    if (path === "prepared") reviewResponse = async () => Response.json({ persisted: true, prepared_continuation: {
      card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0, local_date: new Date().toLocaleDateString("en-CA"),
    } });
    const originalFetcher = fetcher.getMockImplementation()!;
    let acquisitions = 0;
    fetcher.mockImplementation(async (input, options) => {
      if (!String(input).includes("/api/queue/window")) return originalFetcher(input, options);
      acquisitions++;
      if (path === "prepared") throw new DOMException("Fetch is aborted", "AbortError");
      return new Promise(() => {});
    });
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    const activeAttempt = useTrainingStore.getState().attempt;
    await act(async () => { refreshIntervals.forEach(refresh => refresh()); });
    if (path === "fallback") {
      expect(acquisitions).toBe(1);
      await act(async () => { await advanceStudyTimers(15_001); await advanceStudyTimers(15_001); });
      expect(acquisitions).toBe(2);
      expect(trainingProps.reviewPersistenceState).toBe("queueFailed");
    } else {
      expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
      expect(useTrainingStore.getState().queueReadiness).toBe("ready");
      expect(useTrainingStore.getState().serviceError).toBe("");
      expect(useBoardShellStore.getState().board.interactionMode).not.toBe("readonly");
    }
    expect(useTrainingStore.getState().attempt).toEqual(activeAttempt);
    expect(reviewPosts()).toHaveLength(1);
    expect(notifications().find(record => record.key === "review-queue:12")?.active).toBe(false);
  } finally { intervalSpy.mockRestore(); }
});

it.each(["abort", "deadline"])("issue186_prepared_card_survives_aborted_reconciliation_and_recovers: %s", async failure => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13), queueCard("card-c", 14)];
  await readyHome();
  reviewResponse = async () => Response.json({ persisted: true, prepared_continuation: {
    card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0, local_date: new Date().toLocaleDateString("en-CA"),
  } });
  const originalFetcher = fetcher.getMockImplementation()!;
  let unavailable = true;
  fetcher.mockImplementation(async (input, options) => {
    if (unavailable && String(input).includes("/api/queue/window")) {
      if (failure === "deadline") return new Promise(() => {});
      throw new DOMException("Fetch is aborted", "AbortError");
    }
    return originalFetcher(input, options);
  });
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  const preparedAttempt = useTrainingStore.getState().attempt;
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(useBoardShellStore.getState().board.interactionMode).not.toBe("readonly");
  expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false });
  if (failure === "deadline") await act(async () => { await advanceStudyTimers(15_001); });
  expect(useTrainingStore.getState().attempt).toEqual(preparedAttempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  unavailable = false;
  queue = queue.slice(1);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry loading the queue" })); });
  expect(useTrainingStore.getState().attempt).toEqual(preparedAttempt);
  expect(useTrainingStore.getState().practiceCards.map(card => card.queueEntryId)).toEqual([13, 14]);
  expect(reviewPosts()).toHaveLength(1);
});

it.each(["completed head", "malformed JSON", "invalid head", "partial envelope", "inconsistent count", "conflicted head"])(
  "issue186_invalid_authoritative_head_requires_recovery: %s", async invalid => {
    await readyHome();
    if (invalid === "conflicted head") await act(async () => { await enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
      attemptId: "conflicted-next", outcome: "again", guided: false, state: "conflicted",
      conflict: { code: "queue_attempt_unprovable", message: "Saved next-card attempt needs attention", retryable: false } }); });
    const originalFetcher = fetcher.getMockImplementation()!;
    let queueReads = 0;
    fetcher.mockImplementation(async (input, options) => {
      if (!String(input).includes("/api/queue/window")) return originalFetcher(input, options);
      queueReads++;
      if (invalid === "conflicted head") return Response.json({ count: 2, cards: [queueCard("card-b", 13), queueCard("card-c", 14)] });
      if (invalid === "malformed JSON") return new Response("{truncated");
      if (invalid === "partial envelope") return Response.json({ count: 1 });
      if (invalid === "inconsistent count") return Response.json({ count: 0, cards: [queueCard("card-b", 13)] });
      if (invalid === "invalid head") return Response.json({ count: 2, cards: [{ id: "unusable" }, queueCard("card-b", 13)] });
      return Response.json({ count: 2, cards: [queueCard("card-a", 12), queueCard("card-b", 13)] });
    });
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
    expect(trainingProps.reviewPersistenceState).toBe("queueFailed");
    expect(queueReads).toBe(1);
    expect(reviewPosts()).toHaveLength(1);
    expect(notifications().find(record => record.key === "review-queue:12")).toMatchObject({ active: false });
  });

it("issue186_view_replacement_cannot_orphan_progression_notification", async () => {
  await readyHome();
  const originalFetcher = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (input, options) => String(input).includes("/api/queue/window")
    ? new Promise(() => {}) : originalFetcher(input, options));
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(notifications().find(record => record.key === "review-queue:12")?.active).toBe(true);
  cleanup();
  await act(async () => { await advanceStudyTimers(15_001); await advanceStudyTimers(15_001); });
  expect(notifications().find(record => record.key === "review-queue:12")?.active).toBe(false);
  expect(reviewPosts()).toHaveLength(1);
});

it("issue186_cancelled_reconciliation_cannot_update_replaced_view", async () => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13)];
  await readyHome();
  reviewResponse = async () => Response.json({ persisted: true, prepared_continuation: {
    card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0, local_date: new Date().toLocaleDateString("en-CA"),
  } });
  const originalFetcher = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (input, options) => String(input).includes("/api/queue/window")
    ? new Promise(() => {}) : originalFetcher(input, options));
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  const settledNotice = { ...notifications().find(record => record.key === "review-queue:12")! };
  expect(settledNotice).toMatchObject({ active: false, severity: "success" });
  cleanup();
  await act(async () => { await advanceStudyTimers(15_001); await advanceStudyTimers(15_001); });
  expect(notifications().find(record => record.key === "review-queue:12")).toEqual(settledNotice);
  expect(reviewPosts()).toHaveLength(1);
});

it("issue186_lost_review_response_receipt_recovery_uses_queue_only_retry", async () => {
  await readyHome();
  let receiptReady = false;
  reviewResponse = async url => url.includes("/api/operations/")
    ? receiptReady ? Response.json({ state: "complete", response: { persisted: true } }) : new Response(null, { status: 404 })
    : Promise.reject(new TypeError("Committed review response lost"));
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(pendingReviews()).toHaveLength(1);
  const originalAttempt = pendingReviews()[0].attemptId;
  receiptReady = true;
  const originalFetcher = fetcher.getMockImplementation()!;
  let queueUnavailable = true;
  fetcher.mockImplementation(async (input, options) => String(input).includes("/api/queue/window") && queueUnavailable
    ? Promise.reject(new DOMException("Fetch is aborted", "AbortError")) : originalFetcher(input, options));
  await recoverIdle();
  await waitFor(() => expect(trainingProps.reviewPersistenceState).toBe("queueFailed"));
  expect(pendingReviews()).toEqual([]);
  expect(reviewPosts()).toHaveLength(1);
  expect(notifications().find(record => record.key === `review-save:${originalAttempt}`)?.resolvedAt).not.toBeNull();
  queueUnavailable = false;
  queue = [queueCard("card-b", 13)];
  await act(async () => { trainingProps.retryQueueAfterReview!(); await settleStudyStorageTasks(); });
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
  expect(reviewPosts()).toHaveLength(1);
});

it("issue186_stale_progression_cannot_replace_new_attempt", async () => {
  await readyHome();
  const originalFetcher = fetcher.getMockImplementation()!;
  let finishQueue!: (response: Response) => void;
  fetcher.mockImplementation(async (input, options) => String(input).includes("/api/queue/window")
    ? new Promise(resolve => { finishQueue = resolve; }) : originalFetcher(input, options));
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  act(() => {
    useTrainingStore.getState().hydrateLocalQueue([mapQueueCardToPracticeCard(queueCard("newer-card", 99))], true, 1);
    useTrainingStore.setState({ queueReadiness: "ready" });
  });
  const newerAttempt = useTrainingStore.getState().attempt;
  await act(async () => { finishQueue(Response.json({ cards: [queueCard("old-next", 13)], count: 1 })); });
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(99);
  expect(useTrainingStore.getState().attempt).toEqual(newerAttempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(trainingProps.reviewPersistenceState).toBe("idle");
  expect(notifications().find(record => record.key === "review-queue:12")?.active).toBe(false);
});

async function readyHome() {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  await screen.findByRole("button", { name: "Correct" });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
}
async function recoverIdle() {
  await act(async () => { await advanceStudyTimers(0); });
  await act(async () => { await advanceStudyTimers(1000); });
  await act(async () => {
    const scheduled = [...idleCallbacks.values()]; idleCallbacks.clear();
    for (const callback of scheduled) callback({ didTimeout: false, timeRemaining: () => 50 });
    await settleStudyStorageTasks();
  });
  await act(async () => { await advanceStudyTimers(0); });
  vi.useRealTimers();
}
const reviewPosts = () => fetcher.mock.calls.filter(([url, options]) => String(url).endsWith("/review") && options?.method === "POST");
const snapshotActive = () => {
  const state = useTrainingStore.getState();
  return { card: state.getCard(), attempt: state.attempt, fen: state.currentFenString, step: state.step };
};

it.each([false, true])("flush_settlement_audit Home foreground settlement and idle observer retain ownership during reconnect=%s", async reconnect => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13), queueCard("card-c", 14)];
  await readyHome();
  const attempt = useTrainingStore.getState().attempt;
  const preparedNext = { card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0,
    local_date: new Date().toLocaleDateString("en-CA") };
  await enqueuePendingReview({ backendId: "card-a", queueEntryId: 12, attemptId: attempt.attemptId!, outcome: "correct", guided: false, preparedNext });
  await enqueuePendingReview({ backendId: "unrelated-later", queueEntryId: 999, attemptId: "audit-later", outcome: "correct", guided: false });
  let releaseSubmitted!: (response: Response) => void;
  let releaseLater!: (response: Response) => void;
  const submitted = new Promise<Response>(resolve => { releaseSubmitted = resolve; });
  const later = new Promise<Response>(resolve => { releaseLater = resolve; });
  let laterRequested!: () => void;
  const laterStarted = new Promise<void>(resolve => { laterRequested = resolve; });
  reviewResponse = async (_url, options) => {
    if (JSON.parse(options!.body as string).attempt_id === "audit-later") { laterRequested(); return later; }
    return submitted;
  };
  const idleOwner = flushPendingReviews();
  await act(async () => { trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  const foreground = flushPendingReviews(Infinity, false, undefined, undefined, attempt.attemptId);
  const idleOutcome = idleOwner.catch(error => error);
  try {
    if (reconnect) {
      const originalFetcher = fetcher.getMockImplementation()!;
      fetcher.mockImplementation(async (input, options) => {
        if (String(input).includes("/api/queue/window")) throw new Error("Audit reconnect queue unavailable");
        return originalFetcher(input, options);
      });
      act(() => window.dispatchEvent(new Event("online")));
      expect(useTrainingStore.getState().queueReadiness).toBe("loading");
    }
    releaseSubmitted(Response.json({ persisted: true, prepared_continuation: preparedNext }));
    await act(async () => { await foreground; await laterStarted; });
    const advancedAttempt = useTrainingStore.getState().attempt;
    if (reconnect) {
      expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
      expect(trainingProps.reviewPersistenceState).toBe("queueFailed");
      expect(screen.getByRole("button", { name: "Retry loading the queue" })).toBeTruthy();
    } else {
      expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
      expect(advancedAttempt.attemptId).not.toBe(attempt.attemptId);
      expect(trainingProps.reviewPersistenceState).toBe("idle");
    }
    expect(pendingReviews().map(item => item.attemptId)).toEqual(["audit-later"]);
    await act(async () => { releaseLater(Response.json({ persisted: true })); await idleOwner; });
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(reconnect ? 12 : 13);
    expect(useTrainingStore.getState().attempt).toEqual(advancedAttempt);
    expect(reviewPosts()).toHaveLength(2);
  } finally {
    releaseSubmitted(Response.json({ persisted: true, prepared_continuation: preparedNext }));
    releaseLater(Response.json({ persisted: true }));
    await act(async () => { await idleOutcome; await foreground.catch(() => undefined); });
  }
});
function pendingThenConflict() {
  reviewResponse = async (url) => {
    if (url.includes("review-reconcile%3A")) return new Response(null, { status: 404 });
    if (url.includes("/api/operations/")) return Response.json({ state: "failed", error: {
      status_code: 409, code: "queue_attempt_retired", retryable: false, detail: "Original entry retired" } });
    if (url.endsWith("/review/reconcile")) return Response.json({ persisted: false,
      conflict: { code: "queue_attempt_unprovable", message: "Original result needs review", retryable: false } });
    return Response.json({ detail: "Response uncertain", retryable: true }, { status: 503 });
  };
}

it("prepared_next_card_advances_after_durable_review_without_queue_refresh", async () => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13), queueCard("card-c", 14)];
  await readyHome();
  vi.useRealTimers();
  const originalFetcher = fetcher.getMockImplementation()!;
  let releaseQueue: (() => void) | undefined;
  const queueHeld = new Promise<void>(resolve => { releaseQueue = resolve; });
  fetcher.mockImplementation(async (input, options) => {
    if (String(input).includes("/api/queue/window")) {
      await queueHeld;
      return Response.json({ cards: queue.slice(1), count: 2 });
    }
    return originalFetcher(input, options);
  });
  reviewResponse = async () => Response.json({ persisted: true, prepared_continuation: {
    card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0,
    local_date: new Date().toLocaleDateString("en-CA"),
  } });
  try {
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
    expect(trainingProps.reviewPersistenceState).toBe("idle");
    expect(useBoardShellStore.getState().board.interactionMode).not.toBe("readonly");
    expect(pendingReviews()).toHaveLength(0);
  } finally {
    releaseQueue?.();
    await act(async () => { await queueHeld; });
  }
});

it.each(["missing continuation", "pending review", "conflicted review"])(
  "prepared_reconciliation_failure_keeps_play_then_requires_successor_authorization: %s", async settlement => {
    queue = [queueCard("card-a", 12), queueCard("card-b", 13), queueCard("card-c", 14)];
    await readyHome();
    vi.useRealTimers();
    const originalFetcher = fetcher.getMockImplementation()!;
    let rejectReconciliation!: (error: Error) => void;
    let queueRequests = 0;
    let recovered = false;
    fetcher.mockImplementation(async (input, options) => {
      if (String(input).includes("/api/queue/window")) {
        queueRequests++;
        if (queueRequests === 1) return new Promise<Response>((_resolve, reject) => { rejectReconciliation = reject; });
        if (!recovered) throw new Error("Queue unavailable after the prepared card");
        return Response.json({ cards: queue.slice(2), count: 1, local_date: new Date().toLocaleDateString("en-CA") });
      }
      return originalFetcher(input, options);
    });
    reviewResponse = async () => Response.json({ persisted: true, prepared_continuation: {
      card_id: "card-b", queue_entry_id: 13, revision: 1, cycle: 0,
      local_date: new Date().toLocaleDateString("en-CA"),
    } });
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    await waitFor(() => expect(rejectReconciliation).toBeTypeOf("function"));
    const playedPosition = new Chess(); playedPosition.move("e4");
    act(() => useTrainingStore.setState({ currentFenString: asFenString(playedPosition.fen()), step: 1,
      lastMove: ["e2", "e4"], opponentLastMove: ["e2", "e4"] }));
    const activeBoard = snapshotActive();
    await act(async () => { rejectReconciliation(new Error("Prepared reconciliation failed")); });
    await waitFor(() => expect(screen.getByText("Queue reconciliation is waiting. You can finish the prepared card.")).toBeTruthy());
    expect(snapshotActive()).toEqual(activeBoard);
    expect(useTrainingStore.getState().queueReadiness).toBe("ready");
    expect(useBoardShellStore.getState().board.interactionMode).not.toBe("readonly");
    const submittedAttempt = activeBoard.attempt.attemptId!;
    reviewResponse = async url => {
      if (settlement === "pending review") return url.includes("/api/operations/")
        ? Response.json({ state: "queued" })
        : Response.json({ operation_id: `review-attempt:${submittedAttempt}` }, { status: 202 });
      if (settlement === "conflicted review") return url.endsWith("/review/reconcile")
        ? Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "Attempt needs attention", retryable: false } })
        : Response.json({ detail: "Attempt retired", retryable: false }, { status: 409 });
      return Response.json({ persisted: true });
    };
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    await waitFor(() => expect(trainingProps.reviewPersistenceState).toBe(
      settlement === "pending review" ? "pendingConfirmation" : settlement === "conflicted review" ? "conflicted" : "queueFailed"));
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13);
    expect(useTrainingStore.getState().attempt.attemptId).toBe(submittedAttempt);
    expect(useTrainingStore.getState().currentFenString).toBe(activeBoard.fen);
    expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
    expect(reviewPosts().map(([, options]) => JSON.parse(String(options!.body)).queue_entry_id)).toEqual([12, 13]);
    if (settlement === "missing continuation") expect(notifications().find(record => record.key === "review-queue:13"))
      .toMatchObject({ severity: "warning", active: false, resolvedAt: null });
    if (settlement === "pending review") expect(pendingReviews()[0].attemptId).toBe(submittedAttempt);
    if (settlement === "conflicted review") expect(conflictedReviews()[0].attemptId).toBe(submittedAttempt);
    recovered = true;
    reviewResponse = async () => Response.json({ state: "complete", response: { persisted: true } });
    await act(async () => {
      if (settlement === "pending review") await trainingProps.retryReviewSave!();
      else trainingProps.retryQueueAfterReview!();
      await settleStudyStorageTasks();
    });
    await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(14));
    expect(useTrainingStore.getState().queueReadiness).toBe("ready");
    expect(useTrainingStore.getState().attempt.attemptId).not.toBe(submittedAttempt);
    expect(pendingReviews()).toEqual([]);
    expect(reviewPosts()).toHaveLength(2);
    if (settlement === "missing continuation") {
      const recoveredQueueNotice = notifications().find(record => record.key === "review-queue:13");
      expect(recoveredQueueNotice).toMatchObject({ severity: "success", active: false });
      expect(recoveredQueueNotice?.resolvedAt).not.toBeNull();
    }
  });

it.each(["pending", "network timeout", "failed"])("unconfirmed_review_does_not_advance_prepared_card: %s then successful retry", async failure => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13), queueCard("card-c", 14)];
  await readyHome();
  const attemptedIdentity = useTrainingStore.getState().attempt.attemptId;
  reviewResponse = async url => {
    if (failure === "pending") return url.endsWith("/review")
      ? Response.json({ operation_id: `review-attempt:${attemptedIdentity}` }, { status: 202 })
      : Response.json({ state: "queued" });
    if (failure === "network timeout") throw new DOMException("Review request timed out", "TimeoutError");
    return Response.json({ detail: "Review was rejected", retryable: false }, { status: 422 });
  };
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  expect(pendingReviews()[0].attemptId).toBe(attemptedIdentity);
  queue = queue.slice(1);
  reviewResponse = async () => Response.json({ state: "complete", response: { persisted: true } });
  await act(async () => { await trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
  expect(pendingReviews()).toEqual([]);
});

it("unconfirmed_review_does_not_advance_prepared_card: explicit retry remains available after queue head recovery fails", async () => {
  reviewResponse = async () => Response.json({ detail: "Invalid result", retryable: false }, { status: 422 });
  await readyHome();
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  const original = pendingReviews()[0];
  act(() => useTrainingStore.setState({ serviceError: "The authoritative queue head has unresolved local results.", queueReadiness: "unavailable" }));
  reviewResponse = async url => url.includes("/api/operations/")
    ? new Response(null, { status: 404 }) : Response.json({ persisted: true });
  queue = [queueCard("card-b", 13)];
  await act(async () => { await trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  expect(pendingReviews()).toEqual([]);
  expect(reviewPosts()).toHaveLength(2);
  expect(reviewPosts()[1][1]?.body).toEqual(reviewPosts()[0][1]?.body);
  expect(reviewPosts()[1][1]?.headers).toMatchObject({ "Idempotency-Key": `review-attempt:${original.attemptId}` });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
});

it("PR105 displayed idle conflict releases pending confirmation and retains the original completed result", async () => {
  pendingThenConflict(); await readyHome();
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  const original = pendingReviews()[0];
  expect(trainingProps.reviewPersistenceState).toBe("pendingConfirmation");
  expect(screen.getByRole("button", { name: "Check save" })).toBeTruthy();
  queue = [queueCard("card-b", 13)];
  await recoverIdle();
  await waitFor(() => expect(conflictedReviews()[0]).toMatchObject({ ...original, state: "conflicted" }));
  expect(trainingProps.reviewPersistenceState).toBe("conflicted");
  expect(screen.queryByRole("button", { name: "Check save" })).toBeNull();
  expect(screen.getByRole("button", { name: /Review conflicts/ })).toBeTruthy();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
  expect(useBoardShellStore.getState().board.interactionMode).not.toBe("readonly");
  expect(reviewPosts()).toHaveLength(1);
  const reconciliation = fetcher.mock.calls.find(([url]) => String(url).endsWith("/review/reconcile"))!;
  expect(JSON.parse(String(reconciliation[1]?.body))).toMatchObject({ attempt_id: original.attemptId,
    outcome: original.outcome, recorded_at: original.completedAt });
});

it("PR105 earlier idle conflict preserves an unrelated active board logical attempt and progress", async () => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13)];
  pendingThenConflict(); await readyHome();
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  // Pending connected completion cannot start cached B. Model a separately
  // opened active B, whose progress the older idle recovery must preserve.
  act(() => useTrainingStore.getState().hydrateLocalQueue(
    [mapQueueCardToPracticeCard(queue[1])], true, 1));
  act(() => {
    const position = new Chess(); position.move("e4"); position.move("e5");
    useTrainingStore.getState().setCurrentFenString(asFenString(position.fen()));
    useTrainingStore.getState().setStep(2);
    useTrainingStore.getState().setAttemptPhase("playerTurn");
  });
  const activeBeforeConflict = snapshotActive();
  expect(activeBeforeConflict.step).toBeGreaterThan(0);
  await recoverIdle();
  await waitFor(() => expect(conflictedReviews()).toHaveLength(1));
  expect(trainingProps.reviewPersistenceState).toBe("conflicted");
  expect(snapshotActive()).toEqual(activeBeforeConflict);
  expect(notifications().some(record => record.message === "Result saved.")).toBe(false);
  expect(reviewPosts()).toHaveLength(1);
});

it.each([false, true])("PR105 successful flush cannot credit or advance a suppressed same-card successor independent=%s", async independent => {
  queue = [queueCard("card-a", 12), queueCard("card-b", 13)];
  await readyHome();
  await act(async () => await enqueuePendingReview(suppressedReview));
  if (independent) await act(async () => await enqueuePendingReview({ backendId: "card-c", queueEntryId: 14,
    attemptId: "other-persisted", outcome: "correct", guided: false }));
  const submittedAttemptId = useTrainingStore.getState().attempt.attemptId;
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(pendingReviews()).toHaveLength(independent ? 3 : 2);
  expect(pendingReviews()[0]).toMatchObject(suppressedReview);
  expect(pendingReviews().at(-1)).toMatchObject({ attemptId: submittedAttemptId, queueEntryId: 12, outcome: "correct" });
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  expect(screen.getByText("An earlier result for this card needs attention. Use Check saved reviews to resolve it first.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Check saved reviews" })).toBeTruthy();
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  expect(useTrainingStore.getState().attempt.attemptId).toBe(submittedAttemptId);
  expect(notifications().some(record => record.details?.attemptId === submittedAttemptId && record.message.includes("Result saved"))).toBe(false);
  expect(reviewPosts()).toHaveLength(0);
  const retainedSuccessor = pendingReviews().at(-1)!;
  await act(async () => { trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  expect(screen.getByText("An earlier result for this card needs attention. Use Check saved reviews to resolve it first.")).toBeTruthy();
  expect(pendingReviews().at(-1)).toEqual(retainedSuccessor);
  expect(reviewPosts()).toHaveLength(0);
  reviewResponse = async url => url.includes("/api/operations/") ? new Response(null, { status: 404 }) : Response.json({ persisted: true });
  queue = [queueCard("card-b", 13)];
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Check saved reviews" })); });
  vi.useRealTimers();
  await waitFor(() => expect(pendingReviews()).toEqual([]));
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
  expect(screen.queryByRole("button", { name: "Retry save" })).toBeNull();
  expect(reviewPosts().at(-1)?.[1]?.headers).toMatchObject({ "Idempotency-Key": `review-attempt:${retainedSuccessor.attemptId}` });
});

it("PR105 independent submitted review alone is credited while a suppressed result remains intact", async () => {
  queue = [queueCard("card-b", 13)]; await readyHome();
  await act(async () => await enqueuePendingReview(suppressedReview));
  const submittedAttemptId = useTrainingStore.getState().attempt.attemptId;
  queue = [queueCard("card-c", 14)];
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  expect(pendingReviews()).toEqual([suppressedReview]);
  expect(reviewPosts()).toHaveLength(1);
  expect(reviewPosts()[0][1]?.headers).toMatchObject({ "Idempotency-Key": `review-attempt:${submittedAttemptId}` });
  expect(notifications().some(record => record.message.includes("Result saved"))).toBe(true);
  expect(notifications().filter(record => record.message === "Result saved.").every(record => record.details?.attemptId === submittedAttemptId)).toBe(true);
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(14));
});

it("PR105 Home saveFailed allows independent idle receipts without retrying the terminal result", async () => {
  reviewResponse = async url => url.includes("/api/operations/")
    ? Response.json({ state: "complete", response: { persisted: true } })
    : Response.json({ detail: "Invalid result", retryable: false }, { status: 422 });
  await readyHome();
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  const terminal = pendingReviews()[0];
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  await act(async () => await enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "independent-b", outcome: "correct", guided: false }));
  const activeBeforeRecovery = snapshotActive();
  await recoverIdle();
  await waitFor(() => expect(pendingReviews()).toEqual([terminal]));
  expect(snapshotActive()).toEqual(activeBeforeRecovery);
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  expect(reviewPosts()).toHaveLength(1);
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("review-attempt%3Aindependent-b"))).toHaveLength(1);
});

it("PR105 independent idle confirmation waits for a held pointer before changing Home", async () => {
  await readyHome();
  let finishReceipt!: (response: Response) => void;
  reviewResponse = async () => new Promise<Response>(resolve => { finishReceipt = resolve; });
  await act(async () => await enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "independent-held", outcome: "correct", guided: false }));
  await recoverIdle();
  const activeBeforeRecovery = snapshotActive();
  act(() => window.dispatchEvent(new Event("pointerdown")));
  await act(async () => { finishReceipt(Response.json({ state: "complete", response: { persisted: true } })); });
  await waitFor(() => expect(pendingReviews()).toEqual([]));
  expect(snapshotActive()).toEqual(activeBeforeRecovery);
  act(() => window.dispatchEvent(new Event("pointerup")));
  expect(snapshotActive()).toEqual(activeBeforeRecovery);
  expect(trainingProps.reviewPersistenceState).toBe("idle");
});

it("PR105 foreground saving defers independent idle recovery until the save settles", async () => {
  await readyHome();
  let finishSave!: (response: Response) => void;
  reviewResponse = async url => url.includes("/api/operations/")
    ? Response.json({ state: "complete", response: { persisted: true } })
    : new Promise<Response>(resolve => { finishSave = resolve; });
  let saving!: Promise<void>;
  await act(async () => { saving = trainingProps.rateCard("correct"); });
  expect(trainingProps.reviewPersistenceState).toBe("saving");
  await act(async () => await enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "foreground-independent", outcome: "correct", guided: false }));
  await act(async () => { await advanceStudyTimers(1000); });
  expect(idleCallbacks.size).toBe(0);
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/api/operations/"))).toBe(false);
  await act(async () => { finishSave(Response.json({ detail: "Invalid", retryable: false }, { status: 422 })); await saving; });
  await recoverIdle();
  await waitFor(() => expect(pendingReviews()).toHaveLength(1));
  expect(pendingReviews()[0].automaticRecoverySuppressed).toBe("failed");
  expect(reviewPosts()).toHaveLength(1);
});

it("PR105 missing submitted identity cannot become successful confirmation", async () => {
  await readyHome();
  await act(async () => { trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  expect(reviewPosts()).toHaveLength(0);
  expect(notifications().some(record => record.message.includes("Result saved"))).toBe(false);
});

it("PR105 a later independent flush failure cannot erase the submitted persisted outcome", async () => {
  await readyHome();
  let finishSubmitted!: (response: Response) => void;
  reviewResponse = async url => url.endsWith("/card-a/review")
    ? new Promise<Response>(resolve => { finishSubmitted = resolve; })
    : Response.json({ detail: "Other result invalid", retryable: false }, { status: 422 });
  const submittedAttemptId = useTrainingStore.getState().attempt.attemptId;
  let saving!: Promise<void>;
  await act(async () => { saving = trainingProps.rateCard("correct"); });
  await act(async () => await enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "later-independent-failed", outcome: "correct", guided: false }));
  queue = [queueCard("card-c", 14)];
  await act(async () => { finishSubmitted(Response.json({ persisted: true })); await saving; });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(14));
  expect(trainingProps.reviewPersistenceIdentity?.attemptId).toBe(submittedAttemptId);
  expect(pendingReviews()).toMatchObject([{ attemptId: "later-independent-failed" }]);
  await act(async () => { await expect(flushPendingReviews(1)).rejects.toThrow("Other result invalid"); });
  expect(pendingReviews()).toMatchObject([{ attemptId: "later-independent-failed", automaticRecoverySuppressed: "failed" }]);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(14);
  expect(screen.queryByRole("button", { name: "Check save" })).toBeNull();
});

it("PR105 late foreground persistence cannot advance an unrelated replacement attempt", async () => {
  await readyHome();
  let finishSave!: (response: Response) => void;
  reviewResponse = async () => new Promise<Response>(resolve => { finishSave = resolve; });
  let saving!: Promise<void>;
  await act(async () => { saving = trainingProps.rateCard("correct"); });
  act(() => useTrainingStore.getState().hydrateLocalQueue([mapQueueCardToPracticeCard(queueCard("card-b", 13))], true));
  const activeBeforeSave = snapshotActive();
  queue = [queueCard("card-c", 14)];
  await act(async () => { finishSave(Response.json({ persisted: true })); await saving; });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  expect(snapshotActive()).toEqual(activeBeforeSave);
  expect(pendingReviews()).toEqual([]);
});

it("PR105 explicit legacy retry credits the normalized attempt without a new identity", async () => {
  await readyHome();
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([{ ...suppressedReview,
    queueEntryId: 12, attemptId: undefined }]));
  await initializeStudyIntentStorage(true);
  reviewResponse = async url => url.includes("/api/operations/") ? new Response(null, { status: 404 }) : Response.json({ persisted: true });
  await act(async () => { trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  vi.useRealTimers();
  await waitFor(() => expect(pendingReviews()).toEqual([]));
  expect(trainingProps.reviewPersistenceIdentity?.attemptId).toBe("legacy-online:12");
  expect(reviewPosts()[0][1]?.headers).toMatchObject({ "Idempotency-Key": "review-attempt:legacy-online:12" });
});

it.each(["queued", "unconfirmed"])("PR105 completion removed during feedback requires its own persisted receipt state=%s", async receiptState => {
  await readyHome();
  const position = new Chess(); position.move("e4"); position.move("e5");
  act(() => {
    useTrainingStore.getState().setCurrentFenString(asFenString(position.fen()));
    useTrainingStore.getState().setStep(2);
    useTrainingStore.getState().setAttemptPhase("playerTurn");
  });
  const originalAttemptId = useTrainingStore.getState().attempt.attemptId;
  await act(async () => { trainingProps.onMove("g1", "f3"); });
  expect(pendingReviews()).toHaveLength(1);
  await act(async () => { await flushPendingReviews(); });
  expect(pendingReviews()).toEqual([]);
  reviewResponse = async () => Response.json(receiptState === "queued"
    ? { state: "queued" } : { state: "complete", response: { persisted: false } });
  await act(async () => { await advanceStudyTimers(751); });
  expect(trainingProps.reviewPersistenceState).toBe("pendingConfirmation");
  expect(useTrainingStore.getState().attempt.attemptId).toBe(originalAttemptId);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  expect(reviewPosts()).toHaveLength(1);
  reviewResponse = async () => Response.json({ state: "complete", response: { persisted: true } });
  queue = [queueCard("card-b", 13)];
  await act(async () => { trainingProps.retryReviewSave!(); await settleStudyStorageTasks(); });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(13));
  expect(reviewPosts()).toHaveLength(1);
  expect(trainingProps.reviewPersistenceIdentity?.attemptId).toBe(originalAttemptId);
});


it("PR105 non-replay save failure retains raw diagnostics in one attempt-owned incident", async () => {
  clearDebugErrors();
  await readyHome(); vi.useRealTimers();
  clearDebugErrors();
  const attemptId = useTrainingStore.getState().attempt.attemptId!;
  const failure = new DOMException("Browser denied retaining the completed review", "SecurityError");
  const originalWrite = Storage.prototype.setItem;
  const storageWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (key === "tempo-pending-training-reviews-v1") throw failure;
    originalWrite.call(this, key, value);
  });
  try {
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
    expect(document.querySelector(".review-save-status")?.textContent).toContain("open Notifications for details");
    expect(document.querySelector(".review-save-status")?.textContent).not.toContain(failure.message);
    const saveDiagnostics = debugErrors().filter(record => record.context.source === "training-review-save");
    expect(saveDiagnostics).toEqual([expect.objectContaining({ name: "SecurityError", message: failure.message,
      context: expect.objectContaining({ attemptId }) })]);
    expect(buildDebugBundle()).toContain(failure.message);
    const incidents = () => notifications().filter(record => record.source === "training review");
    expect(incidents()).toHaveLength(1);
    expect(incidents()[0]).toMatchObject({ key: `review-save:${attemptId}`, occurrenceCount: 1,
      details: { error: failure.message, errorName: "SecurityError", debugRecordId: saveDiagnostics[0].id } });
    act(() => useTrainingStore.getState().setQueueNotice("Unrelated projection"));
    expect(incidents()).toHaveLength(1); expect(incidents()[0].occurrenceCount).toBe(1);
    const receiptReads = () => fetcher.mock.calls.filter(([url]) => String(url).includes("/api/operations/"));
    expect(receiptReads()).toEqual([]);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Retry save" })); });
    // Diagnostics cannot create a retained result or change the pre-retention retry path.
    expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
    expect(receiptReads()).toEqual([]);
    expect(incidents()).toHaveLength(1); expect(incidents()[0].occurrenceCount).toBe(1);
    expect(reviewPosts()).toEqual([]);
  } finally { storageWrite.mockRestore(); }
});


it("PR105 non-replay replacement save failure keeps diagnostics off the prior saved attempt", async () => {
  queue = [queueCard("card-a", 11), queueCard("card-b", 12)];
  reviewResponse = async (url, options) => {
    if (url.endsWith("/review") && options?.body) {
      const completed = JSON.parse(String(options.body)) as { queue_entry_id: number };
      queue = queue.filter(card => card.queue_entry_id !== completed.queue_entry_id);
    }
    return Response.json({ persisted: true });
  };
  await readyHome(); vi.useRealTimers();
  const earlierAttemptId = useTrainingStore.getState().attempt.attemptId;
  await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
  await waitFor(() => expect(useTrainingStore.getState().getCard().backendId).toBe("card-b"));
  const failedAttemptId = useTrainingStore.getState().attempt.attemptId;
  expect(failedAttemptId).not.toBe(earlierAttemptId);
  const failure = new Error("Replacement review retention denied");
  const originalWrite = Storage.prototype.setItem;
  const storageWrite = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (key === "tempo-pending-training-reviews-v1") throw failure;
    originalWrite.call(this, key, value);
  });
  try {
    await act(async () => { await trainingProps.rateCard("correct"); await settleStudyStorageTasks(); });
    expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
    const failures = notifications().filter(record => record.source === "training review" && record.severity === "error" && !record.resolvedAt);
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ key: `review-save:${failedAttemptId}`, details: { attemptId: failedAttemptId, error: failure.message } });
    expect(notifications().find(record => record.key === `review-save:${earlierAttemptId}`)?.severity).not.toBe("error");
  } finally { storageWrite.mockRestore(); }
});
