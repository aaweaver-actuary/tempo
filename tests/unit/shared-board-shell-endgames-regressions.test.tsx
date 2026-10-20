import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { probeTablebase } from "../../app/utils/tablebase";
import EndgamesView from "../../app/views/endgames_view";
import { useBoardShellStore } from "../../app/state/board-shell-store";

vi.mock("../../app/components/chessboard", () => ({
  Chessboard: () => <div data-testid="board" />,
}));

vi.mock("../../app/utils/local", () => ({
  usesLocalApi: () => false,
}));

vi.mock("../../app/utils/tablebase", () => ({
  probeTablebase: vi.fn(async () => ({ category: "draw", moves: [] })),
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

it("saved material-only endgames show the stored position without calculation or fabricated grading", async () => {
  const { asCardId, asFenString } = await import("../../app/types");
  const onReview = vi.fn();
  const onBury = vi.fn(async () => undefined);
  const fen = "8/8/8/8/8/2k5/R7/1K6 w - - 0 1";
  render(<EndgamesView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} useSharedBoard
    scheduledCard={{ id: asCardId("saved"), kind: "endgame", title: "Saved rook ending", subtitle: "",
      startingFen: asFenString(fen), moves: [], userMoveTarget: 0 }} onReview={onReview} onBury={onBury} />);
  expect(screen.getByRole("status").textContent).toContain("no playable stored solution");
  expect(useBoardShellStore.getState().board.fen).toBe(fen);
  expect(useBoardShellStore.getState().board.interactionMode).toBe("readonly");
  expect(screen.queryByRole("button", { name: /New position/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Correct" })).toBeNull();
  expect(vi.mocked(probeTablebase)).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Bury" }));
  await waitFor(() => expect(onBury).toHaveBeenCalledOnce());
  expect(onReview).not.toHaveBeenCalled();
});

it("standalone endgames mount without provider, position or template requests", () => {
  const request = vi.spyOn(globalThis, "fetch");
  render(<EndgamesView theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("calculation has been removed");
  expect(screen.queryByRole("button", { name: /Add to daily training|New position|Edit material/ })).toBeNull();
  expect(request).not.toHaveBeenCalled();
  request.mockRestore();
});
