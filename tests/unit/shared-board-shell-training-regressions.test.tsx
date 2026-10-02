import { render, screen, waitFor } from "@testing-library/react";
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
  };
  const view = render(<TrainingView {...props} reviewPersistenceState="saving" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="saveFailed" reviewSaveError="Retry this review." />);
  expect(notifications()[0]).toMatchObject({ severity: "error", active: false, resolvedAt: null });
  view.rerender(<TrainingView {...props} reviewPersistenceState="saving" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="saved" />);
  expect(notifications()[0]).toMatchObject({ severity: "success", active: false });
  expect(notifications()[0].resolvedAt).not.toBeNull();
  view.rerender(<TrainingView {...props} reviewPersistenceState="refreshingQueue" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="queueFailed" />);
  expect(notifications()[0]).toMatchObject({ severity: "warning", active: false, resolvedAt: null });
  view.rerender(<TrainingView {...props} reviewPersistenceState="refreshingQueue" />);
  view.rerender(<TrainingView {...props} reviewPersistenceState="idle" />);
  expect(notifications()[0]).toMatchObject({ severity: "success", active: false });
  expect(notifications()[0].resolvedAt).not.toBeNull();
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
