import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { OfflineReviewConflicts } from "../../app/components/offline-review-conflicts";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { clearNotificationHistory, notifications } from "../../app/lib/notifications";
import { conflictedReviews } from "../../app/lib/review-outbox";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asQueueEntryId, asSanMove } from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));
const conflict = { backendId: "card-a", queueEntryId: 17, attemptId: "completed-a", completedAt: "2026-09-30T12:00:00Z",
  expectedRevision: 1, outcome: "correct", guided: false, state: "conflicted", reconciliationSequence: 1,
  conflict: { code: "card_revision_changed", message: "The card changed after this attempt.", retryable: false } };

beforeEach(() => {
  localStorage.clear();
  clearNotificationHistory();
  vi.unstubAllGlobals();
});

it("online conflict dialog exports original evidence and discards only the selected record", async () => {
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([conflict, { ...conflict, attemptId: "completed-b", queueEntryId: 18 }]));
  render(<OfflineReviewConflicts />);
  fireEvent.click(await screen.findByRole("button", { name: "Review conflicts (2)" }));
  expect(screen.getAllByText(conflict.conflict.message)).toHaveLength(2);
  const exported = JSON.parse((screen.getByRole("textbox", { name: "Conflict data" }) as HTMLTextAreaElement).value);
  expect(exported.onlineReviews[0]).toEqual(conflict);
  fireEvent.click(screen.getAllByRole("button", { name: "Discard saved result" })[0]);
  expect(conflictedReviews().map(review => review.attemptId)).toEqual(["completed-b"]);
});

it("online conflict dialog explicitly retries the immutable logical attempt", async () => {
  localStorage.setItem("tempo-pending-training-reviews-v1", JSON.stringify([conflict]));
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ persisted: true }));
  vi.stubGlobal("fetch", fetchMock);
  render(<OfflineReviewConflicts />);
  fireEvent.click(await screen.findByRole("button", { name: "Review conflicts (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "Retry saved result" }));
  await waitFor(() => expect(conflictedReviews()).toEqual([]));
  expect(fetchMock.mock.calls[0][1].headers["Idempotency-Key"]).toBe("review-reconcile:completed-a:2");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ attempt_id: "completed-a", recorded_at: conflict.completedAt });
});

it("old A failure is attributed to A while current B remains displayed and conflicts never report saved", () => {
  const currentCard = { id: asCardId("card-b"), backendId: asCardId("card-b"), queueEntryId: asQueueEntryId(18),
    kind: "opening" as const, title: "Current B", subtitle: "", startingFen: asFenString(STANDARD_FEN),
    moves: [asSanMove("d4")], userMoveTarget: 1, revision: 1 };
  useTrainingStore.getState().initializeCardState(currentCard);
  const props = { dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 2,
    card: currentCard, boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
    handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(),
    reviewPersistenceIdentity: { backendId: "card-a", queueEntryId: 17, attemptId: "completed-a" } };
  const view = render(<TrainingView {...props} reviewPersistenceState="saveFailed" reviewSaveError="Earlier A is unavailable" />);
  expect(notifications().find(notice => notice.key === "review-save:17")?.details).toMatchObject({ cardId: "card-a", queueEntryId: 17, attemptId: "completed-a" });
  expect(notifications().some(notice => notice.key === "review-save:18")).toBe(false);
  view.rerender(<TrainingView {...props} reviewPersistenceState="conflicted" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="idle" />);
  expect(notifications().some(notice => notice.message.includes("Result saved"))).toBe(false);
  expect(screen.getByTestId("board")).toBeTruthy();
});
