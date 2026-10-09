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
    if (url.includes("/api/queue/window")) return Response.json({ cards: queue, count: queue.length });
    if (url.includes("/api/operations/") || url.endsWith("/review") || url.endsWith("/review/reconcile"))
      return reviewResponse(url, options);
    return Response.json({ providers: [], states: [], lines: [], repertoires: [], discoveries: [] });
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

async function readyHome() {
  render(<Home />);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  await screen.findByRole("button", { name: "Correct" });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
}
async function recoverIdle() {
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  await act(async () => {
    const scheduled = [...idleCallbacks.values()]; idleCallbacks.clear();
    for (const callback of scheduled) callback({ didTimeout: false, timeRemaining: () => 50 });
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  vi.useRealTimers();
}
const reviewPosts = () => fetcher.mock.calls.filter(([url, options]) => String(url).endsWith("/review") && options?.method === "POST");
const snapshotActive = () => {
  const state = useTrainingStore.getState();
  return { card: state.getCard(), attempt: state.attempt, fen: state.currentFenString, step: state.step };
};
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

it("PR105 displayed idle conflict releases pending confirmation and retains the original completed result", async () => {
  pendingThenConflict(); await readyHome();
  await act(async () => { await trainingProps.rateCard("correct"); });
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
  await act(async () => { await trainingProps.rateCard("correct"); });
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
  act(() => enqueuePendingReview(suppressedReview));
  if (independent) act(() => enqueuePendingReview({ backendId: "card-c", queueEntryId: 14,
    attemptId: "other-persisted", outcome: "correct", guided: false }));
  const submittedAttemptId = useTrainingStore.getState().attempt.attemptId;
  await act(async () => { await trainingProps.rateCard("correct"); });
  expect(pendingReviews()).toHaveLength(2);
  expect(pendingReviews()[0]).toMatchObject(suppressedReview);
  expect(pendingReviews()[1]).toMatchObject({ attemptId: submittedAttemptId, queueEntryId: 12, outcome: "correct" });
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  expect(screen.getByText("An earlier result for this card needs attention. Use Check saved reviews to resolve it first.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Check saved reviews" })).toBeTruthy();
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  expect(useTrainingStore.getState().attempt.attemptId).toBe(submittedAttemptId);
  expect(notifications().some(record => record.details?.attemptId === submittedAttemptId && record.message.includes("Result saved"))).toBe(false);
  expect(reviewPosts()).toHaveLength(independent ? 1 : 0);
  if (independent) expect(reviewPosts()[0][1]?.headers).toMatchObject({ "Idempotency-Key": "review-attempt:other-persisted" });
  const retainedSuccessor = pendingReviews()[1];
  await act(async () => { trainingProps.retryReviewSave!(); });
  expect(screen.getByText("An earlier result for this card needs attention. Use Check saved reviews to resolve it first.")).toBeTruthy();
  expect(pendingReviews()[1]).toEqual(retainedSuccessor);
  expect(reviewPosts()).toHaveLength(independent ? 1 : 0);
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
  act(() => enqueuePendingReview(suppressedReview));
  const submittedAttemptId = useTrainingStore.getState().attempt.attemptId;
  queue = [queueCard("card-c", 14)];
  await act(async () => { await trainingProps.rateCard("correct"); });
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
  await act(async () => { await trainingProps.rateCard("correct"); });
  const terminal = pendingReviews()[0];
  expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
  act(() => enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
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
  act(() => enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
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
  act(() => enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "foreground-independent", outcome: "correct", guided: false }));
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
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
  await act(async () => { trainingProps.retryReviewSave!(); });
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
  act(() => enqueuePendingReview({ backendId: "card-b", queueEntryId: 13,
    attemptId: "later-independent-failed", outcome: "correct", guided: false }));
  queue = [queueCard("card-c", 14)];
  await act(async () => { finishSubmitted(Response.json({ persisted: true })); await saving; });
  vi.useRealTimers();
  await waitFor(() => expect(useTrainingStore.getState().getCard().queueEntryId).toBe(14));
  expect(trainingProps.reviewPersistenceIdentity?.attemptId).toBe(submittedAttemptId);
  expect(pendingReviews()).toMatchObject([{ attemptId: "later-independent-failed", automaticRecoverySuppressed: "failed" }]);
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
  reviewResponse = async url => url.includes("/api/operations/") ? new Response(null, { status: 404 }) : Response.json({ persisted: true });
  await act(async () => { trainingProps.retryReviewSave!(); });
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
  await act(async () => { await vi.advanceTimersByTimeAsync(751); });
  expect(trainingProps.reviewPersistenceState).toBe("pendingConfirmation");
  expect(useTrainingStore.getState().attempt.attemptId).toBe(originalAttemptId);
  expect(useTrainingStore.getState().getCard().queueEntryId).toBe(12);
  expect(reviewPosts()).toHaveLength(1);
  reviewResponse = async () => Response.json({ state: "complete", response: { persisted: true } });
  queue = [queueCard("card-b", 13)];
  await act(async () => { trainingProps.retryReviewSave!(); });
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
    await act(async () => { await trainingProps.rateCard("correct"); });
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
  await act(async () => { await trainingProps.rateCard("correct"); });
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
    await act(async () => { await trainingProps.rateCard("correct"); });
    expect(trainingProps.reviewPersistenceState).toBe("saveFailed");
    const failures = notifications().filter(record => record.source === "training review" && record.severity === "error" && !record.resolvedAt);
    expect(failures).toHaveLength(1);
    expect(failures[0]).toMatchObject({ key: `review-save:${failedAttemptId}`, details: { attemptId: failedAttemptId, error: failure.message } });
    expect(notifications().find(record => record.key === `review-save:${earlierAttemptId}`)?.severity).not.toBe("error");
  } finally { storageWrite.mockRestore(); }
});
