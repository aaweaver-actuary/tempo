import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { STANDARD_FEN } from "../../app/const";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { clearNotificationHistory, notifications, notificationToasts, publishNotification } from "../../app/lib/notifications";


vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));
beforeEach(() => { localStorage.clear(); clearNotificationHistory(); useTrainingStore.setState(useTrainingStore.getInitialState(), true);
  useTrainingStore.setState({ cardsLeft: 1, currentFenString: asFenString(STANDARD_FEN) }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("phone pending review uses inline confirmation status and no error popup", () => {
  const retry = vi.fn();
  const props = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
    card: { id: asCardId("pending-save"), kind: "opening" as const, title: "Prep", subtitle: "",
      startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const },
    boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
    handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(), useSharedBoard: true,
    reviewPersistenceIdentity: { backendId: "card", queueEntryId: 42, attemptId: "original-attempt" }, retryReviewSave: retry };
  const mounted = render(<TrainingView {...props} reviewPersistenceState="saving" />);
  // Pending is a separate persistence state, not a confirmed failure.
  mounted.rerender(<TrainingView {...props} reviewPersistenceState="pendingConfirmation" />);
  expect(screen.getByText("Waiting for the computer to confirm this result.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Check save" })); expect(retry).toHaveBeenCalledOnce();
  expect(notificationToasts()).toEqual([]);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ severity: "info", resolvedAt: null });
  mounted.rerender(<TrainingView {...props} reviewPersistenceState="saved" />);
  expect(notifications()[0].resolvedAt).not.toBeNull();
});

it("phone review diagnostics retain raw errors without publishing a second incident", async () => {
  const { clearDebugErrors, debugErrors, reportDebugError } = await import("../../app/lib/debug-reporting");
  clearDebugErrors();
  reportDebugError(new Error("Save is still pending (operation review-attempt:original-attempt)."),
    { source: "training-review-replay", attemptId: "original-attempt", notify: false });
  expect(debugErrors()[0]).toMatchObject({ message: expect.stringContaining("review-attempt:original-attempt"), context: { attemptId: "original-attempt" } });
  expect(notifications()).toEqual([]);
});

it("phone passive queue refresh leaves review delivery to bounded receipt recovery", async () => {
  const { fetchAndInitializeQueue } = await import("../../app/views/fetchAndInitializeQueue");
  const { enqueuePendingReview, pendingReviews } = await import("../../app/lib/review-outbox");
  await enqueuePendingReview({ backendId: "retained", queueEntryId: 81, attemptId: "passive", outcome: "correct", guided: false });
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ cards: [], count: 0 }));
  vi.stubGlobal("fetch", fetcher);
  await fetchAndInitializeQueue(false, { preparePhoneQueue: false, replaySavedReviews: false });
  expect(fetcher.mock.calls.every(call => !String(call[0]).includes("/review") && !String(call[0]).includes("/operations/"))).toBe(true);
  expect(pendingReviews()).toHaveLength(1);
  expect(useTrainingStore.getState().pendingReviewError).toContain("waiting for confirmation");
});

it("phone save failure preserves the outbox storage warning and raw details", () => {
  publishNotification({ key: "review-save:storage-attempt", source: "training review", severity: "warning",
    message: "Storage needs attention", details: { classification: "storage", error: "Storage denied" } });
  render(<TrainingView dateLabel="Today" serviceError="" refreshDatabaseQueue={vi.fn()} cardsLeft={1}
    card={{ id: asCardId("storage-card"), kind: "opening", title: "Prep", subtitle: "", startingFen: asFenString(STANDARD_FEN),
      moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" }} boardTheme="brown" pieceSet="cburnett"
    rateCard={vi.fn(async () => undefined)} handleAttemptFailure={vi.fn()} resetCardAttempt={vi.fn()} setEditorCard={vi.fn()}
    onMove={vi.fn()} useSharedBoard reviewPersistenceState="saveFailed" reviewSaveError="This browser could not update the saved result."
    reviewPersistenceIdentity={{ backendId: "storage-card", queueEntryId: 42, attemptId: "storage-attempt" }} retryReviewSave={vi.fn()} />);
  expect(notifications()).toHaveLength(1);
  expect(notifications()[0]).toMatchObject({ severity: "warning", details: { classification: "storage", error: "Storage denied" } });
});
