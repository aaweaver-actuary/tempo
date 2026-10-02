import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Chess } from "chess.js";
import { probeTablebase } from "../../app/utils/tablebase";
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

it("endgame shortcuts browse only actual played moves without probing or grading again", async () => {
  render(<EndgamesView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} useSharedBoard />);
  await waitFor(() => expect(screen.getByText("Classify the position before playing.")).toBeTruthy());
  const originalFen = useBoardShellStore.getState().board.fen;
  act(() => useBoardShellStore.getState().board.keyboard?.end?.());
  expect(useBoardShellStore.getState().board.fen).toBe(originalFen);
  fireEvent.click(screen.getByRole("button", { name: "Draw" }));
  await act(async () => { await useBoardShellStore.getState().board.onMove?.("a2", "a3"); });
  const played = new Chess(originalFen); played.move("a3");
  expect(useBoardShellStore.getState().board.fen).toBe(played.fen());
  const requestCount = vi.mocked(probeTablebase).mock.calls.length;
  act(() => useBoardShellStore.getState().board.keyboard?.start?.());
  expect(useBoardShellStore.getState().board.fen).toBe(originalFen);
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  act(() => useBoardShellStore.getState().board.keyboard?.reset?.());
  expect(useBoardShellStore.getState().board.fen).toBe(played.fen());
  expect(useBoardShellStore.getState().board.interactionMode).toBe("legal");
  expect(vi.mocked(probeTablebase).mock.calls.length).toBe(requestCount);
});
