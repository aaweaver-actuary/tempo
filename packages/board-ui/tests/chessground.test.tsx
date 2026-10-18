import { act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Board } from "../src/Board.js";
import type { BoardHandle, BoardProps } from "../src/types.js";

// The real pinned Chessground module, mounted in jsdom. This is not native browser proof.
const props: BoardProps = { fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1", positionIdentity: "first",
  positionRevision: 0, orientation: "white", turn: "white", check: false, mode: "legal",
  destinations: new Map([["e2", ["e3", "e4"]]]) };
let root: Root | undefined;
let container: HTMLDivElement;
const board = createRef<BoardHandle>();
function mount(initial: BoardProps) {
  vi.useFakeTimers(); vi.stubGlobal("matchMedia", () => ({ matches: true }));
  container = document.createElement("div"); document.body.append(container);
  root = createRoot(container); act(() => root!.render(<Board {...initial} ref={board} />));
}
afterEach(() => {
  act(() => root?.unmount()); root = undefined; container?.remove();
  vi.clearAllTimers(); vi.useRealTimers();
});

it("BUI-15 real Chessground preserves selection across equivalent renders and delivers the latest legal handler once", async () => {
  const first = vi.fn(); const latest = vi.fn(); mount({ ...props, onMove: first });
  act(() => board.current!.selectSquare("e2"));
  act(() => root!.render(<Board {...props} ref={board} onMove={latest} theme="blue" lastMove={["a1", "a2"]} />));
  act(() => board.current!.selectSquare("e4"));
  await act(() => vi.advanceTimersByTimeAsync(30));
  expect(first).not.toHaveBeenCalled();
  expect(latest).toHaveBeenCalledExactlyOnceWith({ from: "e2", to: "e4", positionIdentity: "first", positionRevision: 0 });
  expect([...container.querySelectorAll("piece.pawn.white")].some(piece => (piece as any).cgKey === "e4")).toBe(true);
});

it("BUI-15 real deferred Chessground moves are fenced and pieces restored by same-FEN identity replacement", async () => {
  const handler = vi.fn(); mount({ ...props, onMove: handler });
  act(() => { board.current!.selectSquare("e2"); board.current!.selectSquare("e4"); });
  act(() => root!.render(<Board {...props} ref={board} positionIdentity="replacement" onMove={handler} />));
  await act(() => vi.advanceTimersByTimeAsync(30));
  expect(handler).not.toHaveBeenCalled();
  const pawn = [...container.querySelectorAll("piece.pawn.white")].find(piece => !piece.classList.contains("ghost"));
  expect((pawn as any).cgKey).toBe("e2");
});

it("BUI-15 real Chessground legal destinations and read-only permissions constrain the keyboard seam", async () => {
  const handler = vi.fn(); mount({ ...props, onMove: handler });
  act(() => { board.current!.selectSquare("e2"); board.current!.selectSquare("e5"); });
  await act(() => vi.advanceTimersByTimeAsync(30)); expect(handler).not.toHaveBeenCalled();
  act(() => root!.render(<Board {...props} ref={board} readOnly onMove={handler} />));
  act(() => { board.current!.selectSquare("e2"); board.current!.selectSquare("e4"); });
  await act(() => vi.advanceTimersByTimeAsync(30)); expect(handler).not.toHaveBeenCalled();
});
