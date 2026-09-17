import { render } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chessboard } from "../../app/components/chessboard";
import { STANDARD_FEN } from "../../app/const";

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

it("resize and feedback locks reuse Chessground without toggling construction-only viewOnly or recalculating arrow destinations", () => {
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
  const props = {
    fen: STANDARD_FEN,
    locked: false,
    showHint: false,
    onMove: vi.fn(),
    theme: "brown" as const,
    pieceSet: "cburnett" as const,
  };
  const view = render(<Chessboard {...props} />);
  expect(createBoard).toHaveBeenCalledTimes(1);
  expect(createBoard.mock.calls[0][1]).toMatchObject({ viewOnly: false });
  const destinations = board.set.mock.calls.at(-1)![0].movable.dests;
  view.rerender(
    <Chessboard
      {...props}
      shapes={[{ orig: "e2", dest: "e4", brush: "green" }]}
    />,
  );
  expect(board.set.mock.calls.at(-1)![0].movable.dests).toBe(destinations);
  view.rerender(<Chessboard {...props} locked />);
  view.rerender(<Chessboard {...props} />);
  expect(createBoard).toHaveBeenCalledTimes(1);
  expect(
    board.set.mock.calls.every(([config]) => !("viewOnly" in config)),
  ).toBe(true);
  expect(board.set.mock.calls.at(-1)![0]).toMatchObject({
    draggable: { enabled: true },
    selectable: { enabled: true },
    movable: { color: "white" },
  });
  view.unmount();
  bounds.mockRestore();
});
