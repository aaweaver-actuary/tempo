import { act, fireEvent, render } from "@testing-library/react";
import { Chess } from "chess.js";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Chessboard } from "../../app/components/board/chessboard";
import { STANDARD_FEN } from "../../app/const";
import type { BoardHandle, Square } from "@tempo/board-ui";

// Exercise the actual public handle; renderer-level restoration and generation
// assertions remain owned by package BUI-03/BUI-05 and native BUI-N01..N05.
const captured = vi.hoisted(() => ({ current: undefined as BoardHandle | undefined }));
vi.mock("@tempo/board-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tempo/board-ui")>();
  const React = await import("react");
  return { ...actual, Board: React.forwardRef<BoardHandle, import("@tempo/board-ui").BoardProps>((props, ref) => {
    const inner = React.useRef<BoardHandle>(null);
    React.useImperativeHandle(ref, () => inner.current!);
    React.useLayoutEffect(() => { captured.current = inner.current ?? undefined; return () => { captured.current = undefined; }; }, []);
    return React.createElement(actual.Board, { ...props, ref: inner });
  }) };
});
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));
const props = { owner: "games", positionKey: "finding", positionRevision: 0, fen: STANDARD_FEN,
  locked: false, showHint: false, theme: "brown" as const, pieceSet: "cburnett" as const };
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0, top: 0, left: 0,
    right: 640, bottom: 640, width: 640, height: 640, toJSON: () => ({}) });
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{ width: 640 }] as unknown as DOMRectList);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function userMove(from: Square, to: Square) { act(() => { captured.current!.selectSquare(from); captured.current!.selectSquare(to); }); }
function deliver() { act(() => vi.advanceTimersByTime(1)); }
function authoritative(container: HTMLElement, fen: string, orientation = "white") {
  const board = container.querySelector(".tempo-board")!;
  expect(board.getAttribute("data-fen")).toBe(fen);
  expect(board.getAttribute("data-orientation")).toBe(orientation);
}

it("R restores an applied same-FEN move and rejects its delayed callback without restarting", () => {
  const onMove = vi.fn(), reset = vi.fn();
  const view = render(<Chessboard {...props} onMove={onMove} keyboard={{ reset }} />);
  userMove("e2", "e4"); fireEvent.keyDown(window, { key: "r" }); deliver();
  authoritative(view.container, STANDARD_FEN); expect(onMove).not.toHaveBeenCalled(); expect(reset).toHaveBeenCalledOnce();
});
it("same-FEN locking restores an applied move and fences its deferred callback", () => {
  const old = vi.fn(), current = vi.fn();
  const view = render(<Chessboard {...props} onMove={old} />);
  userMove("e2", "e4");
  view.rerender(<Chessboard {...props} locked onMove={current} />);
  view.rerender(<Chessboard {...props} onMove={current} />); deliver();
  authoritative(view.container, STANDARD_FEN); expect(old).not.toHaveBeenCalled(); expect(current).not.toHaveBeenCalled();
  userMove("d2", "d4"); deliver(); expect(current).toHaveBeenCalledExactlyOnceWith("d2", "d4");
});
it("orientation invalidation restores an applied move before its deferred callback", () => {
  const onMove = vi.fn(); const view = render(<Chessboard {...props} onMove={onMove} />);
  userMove("e2", "e4"); view.rerender(<Chessboard {...props} orientation="black" onMove={onMove} />); deliver();
  authoritative(view.container, STANDARD_FEN, "black"); expect(onMove).not.toHaveBeenCalled();
  userMove("g1", "f3"); deliver(); expect(onMove).toHaveBeenCalledExactlyOnceWith("g1", "f3");
});
it("an accepted move remains authoritative after its handler updates FEN", () => {
  const accepted = vi.fn();
  function Example({ orientation = "white" as const }: { orientation?: "white" | "black" }) {
    const [fen, setFen] = useState(STANDARD_FEN);
    return <Chessboard {...props} fen={fen} orientation={orientation} onMove={(from, to) => {
      accepted(from, to); const chess = new Chess(fen); chess.move({ from, to }); setFen(chess.fen());
    }} />;
  }
  const view = render(<Example />); userMove("e2", "e4"); deliver();
  const chess = new Chess(STANDARD_FEN); chess.move("e4");
  expect(accepted).toHaveBeenCalledExactlyOnceWith("e2", "e4"); authoritative(view.container, chess.fen());
  view.rerender(<Example orientation="black" />); authoritative(view.container, chess.fen(), "black");
});
