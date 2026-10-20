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


it("saved endgame legal lines keep ordinary board practice and review without calculation", async () => {
  const fen = "8/8/8/8/8/2k5/R7/1K6 w - - 0 1";
  useTrainingStore.setState({ currentFenString: asFenString(fen) });
  const onMove = vi.fn();
  const rateCard = vi.fn(async () => undefined);
  render(<TrainingView dateLabel="Today" serviceError="" refreshDatabaseQueue={vi.fn()} cardsLeft={1}
    card={{ id: asCardId("saved-endgame-line"), kind: "endgame", title: "Saved line", subtitle: "",
      startingFen: asFenString(fen), moves: [asSanMove("Ra3+")], userMoveTarget: 1, orientation: "white" }}
    boardTheme="brown" pieceSet="cburnett" rateCard={rateCard} handleAttemptFailure={vi.fn()}
    resetCardAttempt={vi.fn()} setEditorCard={vi.fn()} onMove={onMove} useSharedBoard />);
  expect(useBoardShellStore.getState().board.owner).toBe("train");
  expect(useBoardShellStore.getState().board.fen).toBe(fen);
  expect(screen.queryByText(/no playable stored solution/)).toBeNull();
  useBoardShellStore.getState().board.onMove?.("a2", "a3");
  expect(onMove).toHaveBeenCalledWith("a2", "a3");
  fireEvent.click(screen.getByRole("button", { name: "Correct" }));
  await waitFor(() => expect(rateCard).toHaveBeenCalledWith("correct"));
});

it("material placeholders and illegal stored lines never masquerade as playable endgames", async () => {
  const { hasSavedEndgameLine } = await import("../../app/lib/saved-endgame-practice");
  const card = { id: asCardId("material"), kind: "endgame" as const, title: "Material", subtitle: "",
    startingFen: asFenString(STANDARD_FEN), moves: [], userMoveTarget: 0 };
  expect(hasSavedEndgameLine(card)).toBe(false);
  expect(hasSavedEndgameLine({ ...card, moves: [asSanMove("template:KR:K:white:both")] })).toBe(false);
  expect(hasSavedEndgameLine({ ...card, startingFen: "invalid" as typeof card.startingFen, moves: [asSanMove("e4")] })).toBe(false);
});
