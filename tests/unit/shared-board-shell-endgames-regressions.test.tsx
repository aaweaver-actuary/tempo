import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import EndgamesView from "../../app/views/endgames_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

vi.mock("../../app/utils/local", () => ({
  usesLocalApi: () => false,
}));

vi.mock("../../app/lib/endgame-generator", () => ({
  generateLegalEndgameFen: () =>
    "rn1qkbnr/pppb1ppp/8/3pp3/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 0 3",
}));

vi.mock("../../app/utils/tablebase", () => ({
  probeTablebase: vi.fn(async () => ({ category: "draw", moves: [] })),
  tablebaseCategoryForWhite: vi.fn(() => "draw"),
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

it("Endgames shared board publishes shell ownership and hides local board instance", async () => {
  const view = render(
    <EndgamesView
      theme="brown"
      pieceSet="cburnett"
      onQueueChanged={vi.fn()}
      useSharedBoard
    />,
  );

  await waitFor(() => {
    const shell = useBoardShellStore.getState().board;
    expect(shell.owner).toBe("endgames");
    expect(shell.theme).toBe("brown");
    expect(shell.pieceSet).toBe("cburnett");
  });

  expect(screen.queryByTestId("board")).toBeNull();
  view.unmount();
  expect(useBoardShellStore.getState().board.owner).toBe("train");
});
