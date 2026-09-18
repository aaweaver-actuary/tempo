import { act, render } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { STANDARD_FEN } from "../../app/const";
import { PersistentBoardShell } from "../../app/components/persistent-board-shell";
import {
  defaultBoardState,
  useBoardShellStore,
} from "../../app/state/board-shell-store";

const board = vi.hoisted(() => ({
  set: vi.fn(),
  destroy: vi.fn(),
  redrawAll: vi.fn(),
  setAutoShapes: vi.fn(),
  setShapes: vi.fn(),
}));
const createBoard = vi.hoisted(() =>
  vi.fn((element: unknown, config: unknown) => {
    void element;
    void config;
    return board;
  }),
);

vi.mock("@lichess-org/chessground", () => ({ Chessground: createBoard }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn() }));

beforeEach(() => {
  createBoard.mockClear();
  board.set.mockClear();
  board.destroy.mockClear();
  board.redrawAll.mockClear();
  board.setAutoShapes.mockClear();
  board.setShapes.mockClear();
  useBoardShellStore.setState((state) => ({
    ...state,
    board: {
      ...state.board,
      owner: "train",
      fen: STANDARD_FEN,
      orientation: "white",
      interactionMode: "readonly",
      showHint: false,
      theme: "brown",
      pieceSet: "cburnett",
      shapes: [],
      drawnShapes: [],
      positionRevision: 0,
      onMove: undefined,
      onSquareSelect: undefined,
      onFreeMove: undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    },
  }));
});

it("persistent board shell reuses one Chessground instance across board owner switches", () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const bounds = vi
    .spyOn(HTMLElement.prototype, "getBoundingClientRect")
    .mockReturnValue({
      x: 0,
      y: 80,
      top: 80,
      left: 0,
      right: 640,
      bottom: 720,
      width: 640,
      height: 640,
      toJSON: () => ({}),
    });

  const view = render(<PersistentBoardShell />);
  expect(createBoard).toHaveBeenCalledTimes(1);

  const setBoard = useBoardShellStore.getState().setShellBoardForOwner;
  act(() => {
    setBoard("builder", {
      ...defaultBoardState,
      fen: "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
      orientation: "black",
      interactionMode: "legal",
      positionRevision: 1,
    });
    setBoard("games", {
      ...defaultBoardState,
      fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
      orientation: "white",
      interactionMode: "readonly",
      positionRevision: 2,
    });
    setBoard("tactics", {
      ...defaultBoardState,
      fen: "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
      orientation: "white",
      interactionMode: "legal",
      positionRevision: 3,
    });
    setBoard("endgames", {
      ...defaultBoardState,
      fen: "8/8/8/8/8/8/5K2/6k1 w - - 0 1",
      orientation: "white",
      interactionMode: "readonly",
      positionRevision: 4,
    });
    setBoard("train", {
      ...defaultBoardState,
      fen: STANDARD_FEN,
      orientation: "white",
      interactionMode: "legal",
      positionRevision: 5,
    });
  });

  expect(createBoard).toHaveBeenCalledTimes(1);
  expect(
    view.container
      .querySelector(".persistent-board-shell")
      ?.getAttribute("data-board-owner"),
  ).toBe("train");

  view.unmount();
  expect(board.destroy).toHaveBeenCalledTimes(1);
  bounds.mockRestore();
});
