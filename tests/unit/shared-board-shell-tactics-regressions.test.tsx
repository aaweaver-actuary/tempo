import { render, waitFor, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import TacticsView from "../../app/views/tactics_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { asCardId, asFenString } from "../../app/types";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

vi.mock("../../app/utils/local", () => ({
  usesLocalApi: () => false,
}));

vi.mock("../../app/lib/workspace-data", () => ({
  loadTacticsDeck: vi.fn(async () => [
    {
      record: {
        PuzzleId: "shared-tactics-1",
        DeckId: "hangingPiece-easy",
        DeckPosition: 1,
        FEN: "rn1qkbnr/pppb1ppp/8/3pp3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3",
        Moves: "a2a3",
        Rating: 1200,
      },
      card: {
        id: asCardId("shared-tactics-1"),
        startingFen: asFenString(
          "rn1qkbnr/pppb1ppp/8/3pp3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3",
        ),
        moves: [],
        kind: "puzzle",
        title: "Shared",
        subtitle: "",
        userMoveTarget: 1,
        orientation: "white",
      },
    },
  ]),
  readWorkspaceData: vi.fn(),
  invalidateWorkspaceData: vi.fn(),
}));

beforeEach(() => {
  useBoardShellStore.setState((state) => ({
    ...state,
    board: {
      ...state.board,
      owner: "train",
      interactionMode: "readonly",
      showHint: false,
      shapes: [],
      drawnShapes: [],
    },
  }));
});

it("Tactics shared board publishes shell ownership and hides local board instance", async () => {
  const view = render(
    <TacticsView
      theme="brown"
      pieceSet="cburnett"
      onQueueChanged={vi.fn()}
      useSharedBoard
    />,
  );

  await waitFor(() => {
    const shell = useBoardShellStore.getState().board;
    expect(shell.owner).toBe("tactics");
    expect(shell.theme).toBe("brown");
    expect(shell.pieceSet).toBe("cburnett");
  });

  expect(screen.queryByTestId("board")).toBeNull();
  view.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("train");
});
