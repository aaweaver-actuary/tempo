import { fireEvent, render, waitFor, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import TacticsView from "../../app/views/tactics_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { asCardId, asFenString } from "../../app/types";
import { loadTacticsDeck } from "../../app/lib/workspace-data";

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

it("capture remains available while tactics load or fail and explains durable capture in the demo", async () => {
  vi.mocked(loadTacticsDeck).mockRejectedValueOnce(new Error("Synthetic pack unavailable"));
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  const launch = screen.getByRole("button", { name: "Capture tactic" });
  expect(screen.getByText("Preparing puzzles…")).toBeTruthy();
  await waitFor(() => expect(screen.getByText("Synthetic pack unavailable")).toBeTruthy());
  fireEvent.click(launch);
  expect(screen.getByText(/Durable tactic capture requires local Tempo/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add to training" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Close window" }));
  expect(launch).toBe(document.activeElement);
});

it("capture remains available from Packs", async () => {
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("tab", { name: "Packs" })).toBeTruthy());
  fireEvent.click(screen.getByRole("tab", { name: "Packs" }));
  fireEvent.click(screen.getByRole("button", { name: "Capture tactic" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
});

it("capture remains available after a tactic pack is complete", async () => {
  localStorage.setItem("tempo-tactics-progress-v2", JSON.stringify({
    "hangingPiece-easy-01": { clean: 1, index: 1, cleanIds: ["shared-tactics-1"] },
  }));
  render(<TacticsView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/This pack is complete/)).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Capture tactic" }));
  expect(screen.getByRole("dialog")).toBeTruthy();
});
