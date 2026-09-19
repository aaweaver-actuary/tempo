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

vi.mock("../../app/lib/tactical-catalog", () => ({
  loadTacticalCatalog: vi.fn(async () => ({
    version: 1,
    groups: [{ id: "basic", name: "Basic motifs" }],
    themes: [{ id: "hangingPiece", name: "Hanging pieces", group: "basic" }],
    packs: [
      {
        id: "hangingPiece-easy-01",
        theme: "hangingPiece",
        group: "basic",
        difficulty: "easy",
        ordinal: 1,
        count: 25,
        minRating: 700,
        maxRating: 1100,
        asset: "data/tactics-packs/hangingPiece-easy-01.json",
        legacyDeckId: "hangingPiece-easy",
        active: false,
        clean: 0,
        introduced: 0,
        due: 0,
      },
    ],
  })),
  migrateDemoProgress: vi.fn(async (_catalog, progress) => progress),
  setPackActivation: vi.fn(),
  packProgress: vi.fn(
    (_pack, progress) =>
      progress["hangingPiece-easy-01"] ?? { clean: 0, index: 0 },
  ),
}));

vi.mock("../../app/lib/workspace-data", () => ({
  loadTacticsDeck: vi.fn(async () => [
    {
      record: {
        PuzzleId: "shared-tactics-1",
        DeckId: "hangingPiece-easy-01",
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
