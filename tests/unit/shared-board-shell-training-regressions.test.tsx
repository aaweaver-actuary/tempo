import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { STANDARD_FEN } from "../../app/const";
import { asCardId, asFenString, asSanMove } from "../../app/types";
import { clearNotificationHistory, notifications } from "../../app/lib/notifications";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

beforeEach(() => {
  clearNotificationHistory();
  useBoardShellStore.setState((state) => ({
    ...state,
    board: {
      ...state.board,
      owner: "builder",
      interactionMode: "readonly",
      showHint: false,
      shapes: [],
      drawnShapes: [],
    },
  }));
  useTrainingStore.setState({
    cardsLeft: 1,
    step: 0,
    feedback: "ready",
    showHint: false,
    queueNotice: "",
    isAttemptFailed: false,
    failureAnnotation: undefined,
    failureFen: undefined,
    currentFenString: asFenString(STANDARD_FEN),
    opponentLastMove: undefined,
    lastMove: undefined,
    teachingEncounterKey: null,
    boardAttempt: 0,
    attempt: { entryKey: "shared-train", generation: 0, phase: "playerTurn" },
  });
});

it("failed saves remain actionable after saving progress ends", () => {
  const props = {
    dateLabel: "Today", serviceError: "", refreshDatabaseQueue: vi.fn(), cardsLeft: 1,
    card: { id: asCardId("failed-save"), kind: "opening" as const, title: "Prep", subtitle: "",
      startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" as const },
    boardTheme: "brown" as const, pieceSet: "cburnett" as const, rateCard: vi.fn(async () => undefined),
    handleAttemptFailure: vi.fn(), resetCardAttempt: vi.fn(), setEditorCard: vi.fn(), onMove: vi.fn(), useSharedBoard: true,
    retryReviewSave: vi.fn(), retryQueueAfterReview: vi.fn(),
  };
  const view = render(<TrainingView {...props} reviewPersistenceState="saving" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="saveFailed" reviewSaveError="Retry this review." />);
  expect(notifications()[0]).toMatchObject({ severity: "error", active: false, resolvedAt: null });
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  expect(props.retryReviewSave).toHaveBeenCalledTimes(1);
  view.rerender(<TrainingView {...props} reviewPersistenceState="saving" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="saved" />);
  expect(notifications()[0]).toMatchObject({ severity: "success", active: false });
  expect(notifications()[0].resolvedAt).not.toBeNull();
  const committedSaveNotice = { ...notifications()[0] };
  view.rerender(<TrainingView {...props} reviewPersistenceState="refreshingQueue" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="queueFailed" />);
  expect(screen.getByRole("alert").textContent).toContain("Result saved; the next card could not be loaded.");
  fireEvent.click(screen.getByRole("button", { name: "Retry loading the queue" }));
  expect(props.retryQueueAfterReview).toHaveBeenCalledTimes(1);
  expect(props.rateCard).not.toHaveBeenCalled();
  // Home owns progression; child queue states must retain the settled save.
  expect(notifications()).toEqual([committedSaveNotice]);
  view.rerender(<TrainingView {...props} reviewPersistenceState="refreshingQueue" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="idle" />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(notifications()).toEqual([committedSaveNotice]);
});

it("Training shared board publishes shell ownership and hides local board instance", async () => {
  const view = render(
    <TrainingView
      dateLabel="Today"
      serviceError=""
      refreshDatabaseQueue={vi.fn()}
      cardsLeft={1}
      card={{
        id: asCardId("train-shared"),
        kind: "opening",
        title: "Prep",
        subtitle: "",
        startingFen: asFenString(STANDARD_FEN),
        moves: [asSanMove("e4")],
        userMoveTarget: 1,
        orientation: "white",
      }}
      boardTheme="brown"
      pieceSet="cburnett"
      rateCard={vi.fn(async () => undefined)}
      handleAttemptFailure={vi.fn()}
      resetCardAttempt={vi.fn()}
      setEditorCard={vi.fn()}
      onMove={vi.fn()}
      useSharedBoard
    />,
  );

  await waitFor(() => {
    const shell = useBoardShellStore.getState().board;
    expect(shell.owner).toBe("train");
    expect(shell.theme).toBe("brown");
    expect(shell.pieceSet).toBe("cburnett");
  });

  expect(screen.queryByTestId("board")).toBeNull();
  view.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("train");
});


it.each(["endgame", "defense"] as const)("holds a retired %s card without grading or replacing its attempt", (kind) => {
  const rateCard = vi.fn();
  const refresh = vi.fn();
  const onMove = vi.fn();
  const attempt = useTrainingStore.getState().attempt;
  render(<TrainingView dateLabel="Today" serviceError="" refreshDatabaseQueue={refresh} cardsLeft={1}
    card={{ id: asCardId("retired"), kind, title: "Retired", subtitle: "",
      startingFen: asFenString(STANDARD_FEN), moves: [asSanMove("e4")], userMoveTarget: 1, orientation: "white" }}
    boardTheme="brown" pieceSet="cburnett" rateCard={rateCard} handleAttemptFailure={vi.fn()}
    resetCardAttempt={vi.fn()} setEditorCard={vi.fn()} onMove={onMove} useSharedBoard />);
  expect(screen.getByRole("alert").textContent).toContain("existing attempt has been preserved");
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  expect(useBoardShellStore.getState().board.onMove).toBeUndefined();
  expect(rateCard).not.toHaveBeenCalled();
  expect(refresh).not.toHaveBeenCalled();
  expect(onMove).not.toHaveBeenCalled();
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
});
