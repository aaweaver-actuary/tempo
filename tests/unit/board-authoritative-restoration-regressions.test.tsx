import type { Api } from "@lichess-org/chessground/api";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { act, fireEvent, render } from "@testing-library/react";
import { Chess, type Square } from "chess.js";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Chessboard } from "../../app/components/board/chessboard";
import { STANDARD_FEN } from "../../app/const";

const capturedBoard = vi.hoisted(() => ({ current: undefined as Api | undefined }));
vi.mock("@lichess-org/chessground", async (importOriginal) => {
  const implementation = await importOriginal<typeof import("@lichess-org/chessground")>();
  return { ...implementation, Chessground: (...args: Parameters<typeof implementation.Chessground>) => {
    capturedBoard.current = implementation.Chessground(...args);
    return capturedBoard.current;
  } };
});
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn() }));

const boardProps = { owner: "games", positionKey: "finding", positionRevision: 0,
  fen: STANDARD_FEN, locked: false, showHint: false, theme: "brown" as const,
  pieceSet: "cburnett" as const, orientation: "white" as const };

beforeEach(() => {
  capturedBoard.current = undefined;
  vi.useFakeTimers();
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ x: 0, y: 0,
    top: 0, left: 0, right: 640, bottom: 640, width: 640, height: 640, toJSON: () => ({}) });
  vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{ width: 640 }] as unknown as DOMRectList);
  // Exercise real move/configuration behavior without jsdom animation frames.
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
});

afterEach(() => vi.unstubAllGlobals());

function actualBoard() {
  expect(capturedBoard.current).toBeDefined();
  return capturedBoard.current!;
}

function userMove(from: Key, to: Key) {
  act(() => { actualBoard().selectSquare(from); actualBoard().selectSquare(to); });
}

function deliverDeferredEvents() {
  act(() => vi.advanceTimersByTime(1));
}

function expectAuthoritativePosition(fen: string, lastMove?: readonly [string, string]) {
  const board = actualBoard();
  const chess = new Chess(fen);
  const destinations = new Map<string, string[]>();
  for (const move of chess.moves({ verbose: true })) {
    const targets = destinations.get(move.from) ?? [];
    if (!targets.includes(move.to)) targets.push(move.to);
    destinations.set(move.from, targets);
  }
  expect(board.getFen()).toBe(fen.split(" ")[0]);
  expect(board.state.turnColor).toBe(chess.turn() === "w" ? "white" : "black");
  expect(board.state.movable.dests).toEqual(destinations);
  expect(board.state.check).toBe(chess.isCheck()
    ? chess.findPiece({ type: "k", color: chess.turn() })[0] : undefined);
  expect(board.state.lastMove).toEqual(lastMove);
}

it("R restores an applied same-FEN move and rejects its delayed callback without restarting", () => {
  const onMove = vi.fn(); const reset = vi.fn();
  render(<Chessboard {...boardProps} onMove={onMove} keyboard={{ reset }} />);
  userMove("e2", "e4");
  expect(actualBoard().getFen()).not.toBe(STANDARD_FEN.split(" ")[0]);
  fireEvent.keyDown(window, { key: "r" });
  expectAuthoritativePosition(STANDARD_FEN);
  deliverDeferredEvents();
  expect(onMove).not.toHaveBeenCalled();
  expect(reset).toHaveBeenCalledOnce();
});

it("same-FEN locking restores an applied move and fences its deferred callback", () => {
  const oldHandler = vi.fn();
  const currentHandler = vi.fn();
  const view = render(<Chessboard {...boardProps} onMove={oldHandler} />);
  userMove("e2", "e4");
  const moved = new Chess(STANDARD_FEN);
  moved.move("e4");
  expect(actualBoard().getFen()).toBe(moved.fen().split(" ")[0]);
  expect(actualBoard().state.turnColor).toBe("black");
  expect(actualBoard().state.movable.dests).toBeUndefined();
  expect(oldHandler).not.toHaveBeenCalled();

  view.rerender(<Chessboard {...boardProps} locked onMove={currentHandler} />);
  expectAuthoritativePosition(STANDARD_FEN);
  expect(actualBoard().state.movable.color).toBeUndefined();
  expect(actualBoard().state.draggable.enabled).toBe(false);
  expect(actualBoard().state.selectable.enabled).toBe(false);
  // Unlock before delivery: generation fencing, not just the locked guard,
  // must prevent the old move from reaching the newly committed handler.
  view.rerender(<Chessboard {...boardProps} onMove={currentHandler} />);
  expectAuthoritativePosition(STANDARD_FEN);
  expect(actualBoard().state.movable.color).toBe("white");
  deliverDeferredEvents();
  expect(oldHandler).not.toHaveBeenCalled();
  expect(currentHandler).not.toHaveBeenCalled();
  userMove("d2", "d4");
  deliverDeferredEvents();
  expect(currentHandler).toHaveBeenCalledExactlyOnceWith("d2", "d4");
});

it("orientation invalidation restores an applied move before its deferred callback", () => {
  const onMove = vi.fn();
  const lastMove = ["e7", "e5"] as const;
  const shapes: DrawShape[] = [{ orig: "g1", dest: "f3", brush: "blue" }];
  const drawnShapes: DrawShape[] = [{ orig: "a4", brush: "green" }];
  const props = { ...boardProps, lastMove, shapes, drawnShapes, showHint: true, expectedSan: "e4", onMove };
  const view = render(<Chessboard {...props} />);
  act(() => vi.advanceTimersByTime(0));
  const authoritativeAutoShapes = [...actualBoard().state.drawable.autoShapes];
  expect(authoritativeAutoShapes).toContainEqual({ orig: "e2", dest: "e4", brush: "yellow" });
  userMove("e2", "e4");
  expect(actualBoard().getFen()).not.toBe(STANDARD_FEN.split(" ")[0]);
  expect(actualBoard().state.lastMove).toEqual(["e2", "e4"]);
  expect(onMove).not.toHaveBeenCalled();

  view.rerender(<Chessboard {...props} orientation="black" />);
  expectAuthoritativePosition(STANDARD_FEN, lastMove);
  expect(actualBoard().state.orientation).toBe("black");
  expect(actualBoard().state.movable.color).toBe("white");
  expect(actualBoard().state.drawable.autoShapes).toEqual(authoritativeAutoShapes);
  expect(actualBoard().state.drawable.shapes).toEqual(drawnShapes);
  deliverDeferredEvents();
  expect(onMove).not.toHaveBeenCalled();
  userMove("g1", "f3");
  deliverDeferredEvents();
  expect(onMove).toHaveBeenCalledExactlyOnceWith("g1", "f3");
});

it("an accepted move remains authoritative after its handler updates FEN", () => {
  const acceptedMove = vi.fn();
  function AcceptedPosition({ orientation = "white" }: { orientation?: "white" | "black" }) {
    const [fen, setFen] = useState(STANDARD_FEN);
    const [lastMove, setLastMove] = useState<readonly [string, string]>();
    return <Chessboard {...boardProps} orientation={orientation} fen={fen} lastMove={lastMove} onMove={(from: Square, to: Square) => {
      acceptedMove(from, to);
      const chess = new Chess(fen);
      chess.move({ from, to });
      setFen(chess.fen());
      setLastMove([from, to]);
    }} />;
  }
  const view = render(<AcceptedPosition />);
  userMove("e2", "e4");
  deliverDeferredEvents();
  const accepted = new Chess(STANDARD_FEN);
  accepted.move("e4");
  expect(acceptedMove).toHaveBeenCalledExactlyOnceWith("e2", "e4");
  expectAuthoritativePosition(accepted.fen(), ["e2", "e4"]);
  view.rerender(<AcceptedPosition orientation="black" />);
  expectAuthoritativePosition(accepted.fen(), ["e2", "e4"]);
  expect(actualBoard().state.orientation).toBe("black");
});
