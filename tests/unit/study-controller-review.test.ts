import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { PracticeCard } from "../../app/domain/cards";
import type { PendingReview } from "../../app/lib/review-outbox";
import { useStudyReviewController } from "../../app/features/study";

const mocks = vi.hoisted(() => {
  class ReplayError extends Error {
    flushResult?: { persistedAttemptIds: string[]; conflictedAttemptIds: string[] };
    blocked = false; backendId = "other"; queueEntryId = 2; attemptId = "other";
    classification = "transient"; endpoint = "/review"; status = 503; retryable = true; code = undefined;
  }
  return { reviews: [] as unknown[], enqueue: vi.fn(), flush: vi.fn(), retryNext: vi.fn(), session: vi.fn(), ReplayError };
});
vi.mock("../../app/lib/review-outbox", () => ({
  enqueuePendingReview: mocks.enqueue, flushPendingReviews: mocks.flush,
  logicalAttemptId: (review: PendingReview) => review.attemptId ?? `legacy-online:${review.queueEntryId}`,
  pendingReviews: () => mocks.reviews, recoverableReviews: () => mocks.reviews,
  conflictedReviews: () => [], ReviewReplayError: mocks.ReplayError, reviewStudySession: mocks.session,
}));
vi.mock("../../app/state/training-store", () => ({ useTrainingStore: { getState: () => ({ attempt: { attemptId: "original", generation: 4 } }) } }));
vi.mock("../../app/views/fetchAndInitializeQueue", () => ({ fetchAndInitializeQueue: vi.fn(), rememberConfirmedQueueEntry: vi.fn(), QueueProcessingError: class extends Error {} }));
vi.mock("../../app/lib/review-save-notice", () => ({ reportReviewSaveFailure: vi.fn() }));
vi.mock("../../app/lib/debug-reporting", () => ({ reportDebugError: vi.fn() }));

const card = { id: "a", backendId: "a", queueEntryId: 1, revision: 1 } as PracticeCard;
const review: PendingReview = { backendId: "a", queueEntryId: 1, attemptId: "original", outcome: "correct", guided: false };
beforeEach(() => { mocks.reviews = []; mocks.enqueue.mockReset(); mocks.flush.mockReset(); mocks.retryNext.mockReset(); mocks.session.mockReset().mockResolvedValue({ retryNext: mocks.retryNext }); });
afterEach(cleanup);
function setup() {
  const hook = renderHook(() => useStudyReviewController({ onNextCard: vi.fn(), checkIntegrity: async () => {}, acquireQueue: vi.fn(), rememberConfirmed: vi.fn(), isRetryable: () => true }));
  const confirmed = mocks.retryNext, onEvent = vi.fn();
  const input = { card, attemptId: "original", attemptGeneration: 4, transitionGeneration: 1,
    retryPending: false, recordedAtCompletion: true, prepare: async () => review, onEvent };
  act(() => { hook.result.current.beginReview("test"); });
  return { hook, input, confirmed, onEvent };
}

it("retains through the existing outbox and rejects missing submitted identity", async () => {
  const { hook, input, confirmed } = setup();
  mocks.enqueue.mockResolvedValue("original");
  await act(async () => { expect(await hook.result.current.retain(review)).toBe("original"); });
  expect(mocks.enqueue).toHaveBeenCalledWith(review);
  await act(async () => { await hook.result.current.submit({ ...input, attemptId: undefined }); });
  expect(mocks.flush).not.toHaveBeenCalled();
  expect(confirmed).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot.phase).toBe("saveFailed");
});

it("keeps a submitted receipt when a later unrelated flush item fails", async () => {
  const { hook, input, confirmed, onEvent } = setup();
  const error = new mocks.ReplayError("later failed");
  error.flushResult = { persistedAttemptIds: ["original"], conflictedAttemptIds: [] };
  mocks.flush.mockRejectedValue(error);
  await act(async () => { await hook.result.current.submit(input); });
  expect(confirmed).toHaveBeenCalledWith("review:original", expect.any(Function), expect.any(Function), expect.any(Function));
  expect(onEvent).toHaveBeenCalledWith({ type: "pending-error", message: "later failed" });
});

it("holds a same-card successor behind a suppressed earlier review", async () => {
  const { hook, input, confirmed, onEvent } = setup();
  mocks.reviews = [{ ...review, attemptId: "earlier", automaticRecoverySuppressed: "failed" }, review];
  mocks.flush.mockResolvedValue({ persistedAttemptIds: [], conflictedAttemptIds: [] });
  // The newly retained identity must be explicit, rather than choosing the earlier same-card review.
  mocks.enqueue.mockResolvedValue("original");
  await act(async () => { await hook.result.current.submit({ ...input, recordedAtCompletion: false }); });
  expect(confirmed).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot.phase).toBe("saveFailed");
  expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ type: "pending-error" }));
});

it("does not progress a completion after its transition is superseded", async () => {
  const { hook, input, confirmed, onEvent } = setup();
  let resolve!: (result: unknown) => void;
  mocks.flush.mockImplementation(() => new Promise(done => { resolve = done; }));
  let request!: Promise<void>;
  act(() => { request = hook.result.current.submit(input); });
  onEvent.mockClear();
  act(() => { hook.result.current.beginReview("replacement"); });
  await act(async () => { resolve({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] }); await request; });
  expect(onEvent).not.toHaveBeenCalled();
  expect(confirmed).not.toHaveBeenCalled();
});

it("keeps the completion identity when an earlier same-card review exists", async () => {
  const { hook, input, confirmed } = setup();
  mocks.reviews = [{ ...review, attemptId: "earlier", automaticRecoverySuppressed: "failed" }, review];
  mocks.flush.mockResolvedValue({ persistedAttemptIds: [], conflictedAttemptIds: [] });
  await act(async () => { await hook.result.current.submit(input); });
  expect(mocks.flush).toHaveBeenCalledWith(Infinity, false, undefined, undefined, "original");
  expect(confirmed).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot.phase).toBe("saveFailed");
});

it("checks the original first retained review through the outbox", async () => {
  const { hook } = setup();
  mocks.reviews = [review];
  const result = { persistedAttemptIds: ["original"], conflictedAttemptIds: [] };
  mocks.flush.mockResolvedValue(result);
  await act(async () => { expect(await hook.result.current.checkSavedReviews()).toEqual(result); });
  expect(mocks.flush).toHaveBeenCalledWith(Infinity, true, "original");
});

it("finishes durable flush after unmount without emitting events or progressing", async () => {
  const { hook, input, confirmed, onEvent } = setup();
  let resolve!: (result: unknown) => void;
  mocks.flush.mockImplementation(() => new Promise(done => { resolve = done; }));
  let saving!: Promise<void>;
  act(() => { saving = hook.result.current.submit(input); });
  onEvent.mockClear();
  hook.unmount();
  await act(async () => { resolve({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] }); await saving; });
  expect(mocks.flush).toHaveBeenCalledOnce();
  expect(onEvent).not.toHaveBeenCalled();
  expect(confirmed).not.toHaveBeenCalled();
});

it("suppresses stale identity and events after delayed retention while still flushing the original", async () => {
  const { hook, input, confirmed, onEvent } = setup();
  let resolve!: (id: string) => void;
  mocks.enqueue.mockImplementation(() => new Promise(done => { resolve = done; }));
  mocks.flush.mockResolvedValue({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] });
  let saving!: Promise<void>;
  await act(async () => { saving = hook.result.current.submit({ ...input, recordedAtCompletion: false }); });
  act(() => { hook.result.current.beginReview("replacement"); });
  await act(async () => { resolve("original"); await saving; });
  expect(mocks.flush).toHaveBeenCalledWith(Infinity, false, undefined, undefined, "original");
  expect(hook.result.current.snapshot.identity).toBeUndefined();
  expect(onEvent).not.toHaveBeenCalled();
  expect(confirmed).not.toHaveBeenCalled();
});

it("does not deliver an explicit check result after unmount", async () => {
  const { hook } = setup();
  mocks.reviews = [review];
  let resolve!: (result: unknown) => void;
  mocks.flush.mockImplementation(() => new Promise(done => { resolve = done; }));
  const check = hook.result.current.checkSavedReviews();
  hook.unmount();
  resolve({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] });
  expect(await check).toBeUndefined();
  expect(mocks.flush).toHaveBeenCalledOnce();
});

it("C05 explicit confirmation drops a receipt after its storage authority changes", async () => {
  const storage = await import("../../database/browser/study-intent-storage");
  const current = vi.spyOn(storage, "isStudyIntentStoreCurrent").mockReturnValue(false);
  const { hook, confirmed } = setup();
  mocks.reviews = [review];
  mocks.flush.mockResolvedValue({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] });
  try {
    await act(async () => { expect(await hook.result.current.checkSavedReviews()).toBeUndefined(); });
    expect(mocks.flush).toHaveBeenCalledWith(Infinity, true, "original");
    expect(confirmed).not.toHaveBeenCalled();
  } finally { current.mockRestore(); }
});

it("C05 foreground receipt cannot progress after original storage changes with the same attempt identity", async () => {
  const storage = await import("../../database/browser/study-intent-storage");
  let originalCurrent = true;
  const current = vi.spyOn(storage, "isStudyIntentStoreCurrent").mockImplementation(() => originalCurrent);
  const { hook, input, confirmed, onEvent } = setup();
  let release!: (result: unknown) => void;
  mocks.flush.mockImplementation(() => new Promise(done => { release = done; }));
  try {
    let saving!: Promise<void>;
    await act(async () => { saving = hook.result.current.submit(input); });
    expect(mocks.flush).toHaveBeenCalledWith(Infinity, false, undefined, undefined, "original");
    onEvent.mockClear();
    originalCurrent = false;
    await act(async () => { release({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] }); await saving; });
    expect(confirmed).not.toHaveBeenCalled();
    expect(onEvent).not.toHaveBeenCalled();
    expect(hook.result.current.snapshot.phase).not.toBe("saved");
  } finally { current.mockRestore(); }
});


it("C05 foreground progression cannot enter a replacement session after storage changes", async () => {
  const storage = await import("../../database/browser/study-intent-storage");
  let originalCurrent = true;
  const current = vi.spyOn(storage, "isStudyIntentStoreCurrent").mockImplementation(() => originalCurrent);
  const { hook, input, confirmed } = setup();
  let release!: (value: unknown) => void;
  mocks.session.mockImplementation(() => new Promise(done => { release = done; }));
  mocks.flush.mockResolvedValue({ persistedAttemptIds: ["original"], conflictedAttemptIds: [] });
  try {
    await act(async () => { await hook.result.current.submit(input); });
    expect(mocks.session).toHaveBeenCalledOnce();
    originalCurrent = false;
    await act(async () => { release({ retryNext: confirmed }); await Promise.resolve(); });
    expect(confirmed).not.toHaveBeenCalled();
  } finally { current.mockRestore(); }
});
