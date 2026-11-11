import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createStudySession } from "@tempo/study-session";
import type { IntentStore } from "@tempo/browser-intents";
import type { PracticeCard } from "../../app/domain/cards";
import { useStudyReviewController } from "../../app/features/study";

const state = vi.hoisted(() => ({ attempt: { attemptId: "original", generation: 4 }, session: undefined as any, flush: vi.fn(), store: {} }));
vi.mock("../../app/state/training-store", () => ({ useTrainingStore: { getState: () => ({ attempt: state.attempt }) } }));
vi.mock("../../app/lib/review-outbox", async importOriginal => ({ ...(await importOriginal<typeof import("../../app/lib/review-outbox")>()), reviewStudySession: async () => state.session, flushPendingReviews: state.flush, pendingReviews: () => [], conflictedReviews: () => [] }));
vi.mock("../../app/lib/study-connection", () => ({ studyWorkspace: () => "workspace" }));
vi.mock("../../database/browser/study-intent-storage", () => ({ studyIntentStore: async () => state.store, isStudyIntentStoreCurrent: () => true }));
vi.mock("../../app/lib/prepared-review-continuation", () => ({ advancePreparedContinuation: () => false }));
vi.mock("../../app/lib/workspace-data", () => ({ invalidateWorkspaceData: vi.fn() }));
vi.mock("../../app/lib/notifications", () => ({ notifications: () => [], publishNotification: () => "notice", resolveNotification: vi.fn(), updateNotification: vi.fn() }));
const card = { id: "a", backendId: "a", queueEntryId: 1 } as PracticeCard;
afterEach(() => { cleanup(); state.session?.dispose(); });
function setup(committed = true) {
  state.attempt = { attemptId: "original", generation: 4 };
  const checkpoint = vi.fn(async () => undefined);
  const store = { get: vi.fn(async () => ({ state: committed ? "committed" : "pending" })), checkpoint } as unknown as IntentStore;
  state.session = createStudySession({ store, learning: { deliver: vi.fn() }, owner: "test" });
  state.session.start("review:original");
  const acquireQueue = vi.fn();
  const onNextCard = vi.fn();
  const hook = renderHook(() => useStudyReviewController({ acquireQueue, onNextCard, rememberConfirmed: vi.fn(), checkIntegrity: async () => {}, isRetryable: () => true }));
  act(() => { hook.result.current.beginReview("test"); });
  state.flush.mockResolvedValue({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] });
  const progress = async () => {
    await hook.result.current.submit({ card, attemptId: "original", attemptGeneration: 4, transitionGeneration: 1,
      retryPending: false, recordedAtCompletion: true, prepare: async () => { throw new Error("unexpected preparation"); }, onEvent: vi.fn() });
    await new Promise(resolve => setTimeout(resolve, 0));
  };
  return { hook, acquireQueue, onNextCard, checkpoint, progress };
}
it("uses the existing session checkpoint and two acquisitions, then retries queue only", async () => {
  const { hook, acquireQueue, checkpoint, onNextCard, progress } = setup();
  acquireQueue.mockRejectedValue(new Error("unavailable"));
  await act(async () => { await progress(); });
  expect(acquireQueue).toHaveBeenCalledTimes(2);
  expect(checkpoint).toHaveBeenCalledWith("progression:review:original", { key: "review:original", pending: true });
  expect(hook.result.current.snapshot.phase).toBe("queueFailed");
  acquireQueue.mockResolvedValue("installed");
  await act(async () => { hook.result.current.retryQueueAfterReview(); await new Promise(resolve => setTimeout(resolve, 0)); });
  expect(acquireQueue).toHaveBeenLastCalledWith(true, expect.objectContaining({ replaySavedReviews: false, retryPreparation: true, timeoutMs: 15000, expectedAttempt: { attemptId: "original", generation: 4 } }));
  expect(onNextCard).toHaveBeenCalledOnce();
  expect(checkpoint).toHaveBeenLastCalledWith("progression:review:original", { key: "review:original", pending: false });
});
it("does not acquire without confirmed settlement", async () => {
  const { hook, acquireQueue, progress } = setup(false);
  await act(async () => { await progress(); });
  expect(acquireQueue).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot.phase).toBe("queueFailed");
});
it("does not acquire for a replaced attempt", async () => {
  const { hook, acquireQueue, progress } = setup();
  state.attempt = { attemptId: "new", generation: 5 };
  await act(async () => { await progress(); });
  expect(acquireQueue).not.toHaveBeenCalled();
});
it("aborts the in-flight presentation acquisition on unmount", async () => {
  const { hook, acquireQueue, progress } = setup();
  let signal!: AbortSignal;
  acquireQueue.mockImplementation((_advance, options) => new Promise((_resolve, reject) => {
    signal = options.signal;
    signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }));
  let progression!: Promise<void>;
  await act(async () => { progression = progress(); await new Promise(resolve => setTimeout(resolve, 0)); });
  expect(signal.aborted).toBe(false);
  hook.unmount();
  await act(async () => { await progression; });
  expect(signal.aborted).toBe(true);
});

it("keeps the progression checkpoint pending when queue acquisition is superseded", async () => {
  const { hook, acquireQueue, checkpoint, onNextCard, progress } = setup();
  acquireQueue.mockResolvedValue("superseded");
  await act(async () => { await progress(); });
  expect(acquireQueue).toHaveBeenCalledOnce();
  expect(checkpoint).toHaveBeenCalledWith("progression:review:original", { key: "review:original", pending: true });
  expect(checkpoint).not.toHaveBeenCalledWith("progression:review:original", { key: "review:original", pending: false });
  expect(onNextCard).not.toHaveBeenCalled();
});
it("does not reacquire when a retained progression action is invoked after unmount", async () => {
  const { hook, acquireQueue, checkpoint, progress } = setup();
  state.flush.mockResolvedValue({ persistedAttemptIds: [], conflictedAttemptIds: [] });
  await act(async () => { await progress(); });
  expect(hook.result.current.snapshot.identity?.attemptId).toBe("original");
  const confirm = hook.result.current.captureConfirmation();
  hook.unmount();
  acquireQueue.mockResolvedValue("installed");
  await act(async () => { await confirm({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] }); });
  expect(acquireQueue).not.toHaveBeenCalled();
  expect(checkpoint).not.toHaveBeenCalled();
});
