import { act, render, screen } from "@testing-library/react";
import { Chess } from "chess.js";
import { expect, it, vi } from "vitest";
import TrainingView from "../../app/views/training_view";
import { useTrainingStore } from "../../app/state/training-store";
import { STANDARD_FEN } from "../../app/const";
import {
  asCardId,
  asFenKey,
  asFenString,
  asIsoDateString,
  asRepertoireId,
  asSanMove,
} from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: ({ shapes }: { shapes: unknown[] }) => (
    <div data-testid="board" data-shapes={JSON.stringify(shapes)} />
  ),
}));

it("position notes and annotations reveal only at the failed position and remain hidden in clean training", () => {
  const board = new Chess();
  board.move("e4");
  useTrainingStore.setState({
    currentFenString: asFenString(board.fen()),
    failureFen: asFenString(STANDARD_FEN),
    feedback: "wrong",
    isAttemptFailed: true,
    cardsLeft: 1,
    step: 0,
    attempt: {entryKey:"notes",generation:0,phase:"guided"},
    opponentLastMove: undefined,
    failureAnnotation: {
      repertoireId: asRepertoireId("notes"),
      fenKey: asFenKey(STANDARD_FEN.split(" ").slice(0, 4).join(" ")),
      comment: "Private study note",
      arrows: [{ from: "e2", to: "e4", color: "green" }],
      squares: [],
      updatedAt: asIsoDateString("2026-09-16T00:00:00Z"),
    },
  });
  render(
    <TrainingView
      dateLabel="Today"
      serviceError=""
      refreshDatabaseQueue={vi.fn()}
      cardsLeft={1}
      card={{
        id: asCardId("notes-card"),
        kind: "opening",
        title: "Prep",
        subtitle: "",
        startingFen: asFenString(STANDARD_FEN),
        moves: [asSanMove("e4")],
        userMoveTarget: 1,
      }}
      boardTheme="brown"
      pieceSet="cburnett"
      rateCard={vi.fn(async () => undefined)}
      handleAttemptFailure={vi.fn()}
      resetCardAttempt={vi.fn()}
      setEditorCard={vi.fn()}
      onMove={vi.fn()}
    />,
  );
  expect(screen.queryByText("Private study note")).toBeNull();
  expect(screen.getByTestId("board").getAttribute("data-shapes")).toBe("[]");
  act(() => useTrainingStore.setState({ currentFenString: asFenString(STANDARD_FEN) }));
  expect(screen.getByText("Private study note")).toBeTruthy();
  expect(screen.getByTestId("board").getAttribute("data-shapes")).toContain(
    "e4",
  );
  act(() => useTrainingStore.setState({ isAttemptFailed: false }));
  expect(screen.queryByText("Private study note")).toBeNull();
  expect(screen.getByTestId("board").getAttribute("data-shapes")).toBe("[]");
});
