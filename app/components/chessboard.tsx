"use client";

import { Chessground } from "@lichess-org/chessground";
import type { Api } from "@lichess-org/chessground/api";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { Chess, type Square } from "chess.js";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useBoardViewport } from "../hooks/use-board-viewport";
import { measureTempoOperation } from "../lib/performance";
import { playMoveSound } from "../lib/move-sound";

export type BoardTheme = "brown" | "blue" | "green";
export type PieceSet = "cburnett" | "merida";
const EMPTY_SHAPES: DrawShape[] = [];
const DRAW_BRUSHES = {
  green: { key: "g", color: "#4f8a59", opacity: 0.88, lineWidth: 10 },
  red: { key: "r", color: "#b45f50", opacity: 0.88, lineWidth: 10 },
  blue: { key: "b", color: "#4e7ca8", opacity: 0.88, lineWidth: 10 },
  yellow: { key: "y", color: "#d0a83f", opacity: 0.92, lineWidth: 11 },
  maia: { key: "m", color: "#8a62a5", opacity: 0.9, lineWidth: 10 },
};

type ChessboardProps = {
  fen: string;
  expectedSan?: string;
  lastMove?: readonly [string, string];
  locked: boolean;
  showHint: boolean;
  theme: BoardTheme;
  pieceSet: PieceSet;
  shapes?: DrawShape[];
  drawnShapes?: DrawShape[];
  onDrawnShapesChange?: (shapes: DrawShape[]) => void;
  editMode?: boolean;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onMove: (from: Square, to: Square) => void;
  orientation?: "white" | "black";
  onFlip?: () => void;
  positionRevision?: number;
};

export function Chessboard({
  fen,
  expectedSan,
  lastMove,
  locked,
  showHint,
  theme,
  pieceSet,
  shapes = EMPTY_SHAPES,
  drawnShapes = EMPTY_SHAPES,
  onDrawnShapesChange,
  editMode = false,
  onSquareSelect,
  onFreeMove,
  onMove,
  orientation = "white",
  onFlip,
  positionRevision = 0,
}: ChessboardProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const handlers = useRef({
    onMove,
    onFreeMove,
    onSquareSelect,
    onDrawnShapesChange,
    editMode,
  });
  const surfaceSize = useBoardViewport(hostRef);
  const [flipped, setFlipped] = useState(false);
  const visualOrientation = flipped
    ? orientation === "white"
      ? "black"
      : "white"
    : orientation;
  // One legal-move calculation per position; annotations never repeat this work.
  const position = useMemo(() => {
    const chess = new Chess(fen, { skipValidation: editMode });
    const legalMoves = editMode ? [] : chess.moves({ verbose: true });
    const destinations = new Map<Key, Key[]>();
    for (const move of legalMoves) {
      const from = move.from as Key;
      const targets = destinations.get(from) ?? [];
      if (!targets.includes(move.to as Key)) targets.push(move.to as Key);
      destinations.set(from, targets);
    }
    return { chess, legalMoves, destinations };
  }, [fen, editMode]);
  const positionRef = useRef(position);
  const [preparedHint, setPreparedHint] = useState<{
    fen: string;
    san: string;
    shape?: DrawShape;
  }>();

  useLayoutEffect(() => {
    handlers.current = {
      onMove,
      onFreeMove,
      onSquareSelect,
      onDrawnShapesChange,
      editMode,
    };
    positionRef.current = position;
  }, [
    onMove,
    onFreeMove,
    onSquareSelect,
    onDrawnShapesChange,
    editMode,
    position,
  ]);

  useEffect(() => {
    if (!showHint || !expectedSan) return;
    const timer = window.setTimeout(() => {
      const move = position.legalMoves.find((move) => move.san === expectedSan);
      setPreparedHint({
        fen,
        san: expectedSan,
        shape: move
          ? { orig: move.from as Key, dest: move.to as Key, brush: "yellow" }
          : undefined,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [position, expectedSan, fen, showHint]);
  const hint =
    showHint && preparedHint?.fen === fen && preparedHint.san === expectedSan
      ? preparedHint.shape
      : undefined;

  useEffect(() => {
    const flip = (event: KeyboardEvent) => {
      if (!surfaceRef.current?.getClientRects().length) return;
      const target = event.target as HTMLElement | null;
      if (
        event.key.toLowerCase() !== "f" ||
        target?.matches('input,textarea,select,[contenteditable="true"]')
      )
        return;
      if (
        document.querySelector('[role="dialog"]') &&
        !surfaceRef.current?.closest('[role="dialog"]')
      )
        return;
      event.preventDefault();
      if (onFlip) onFlip();
      else setFlipped((current) => !current);
    };
    window.addEventListener("keydown", flip);
    return () => window.removeEventListener("keydown", flip);
  }, [onFlip]);

  useLayoutEffect(() => {
    if (!surfaceRef.current) return;
    const finishReady = measureTempoOperation("board-ready");
    apiRef.current = Chessground(surfaceRef.current, {
      viewOnly: false,
      coordinates: true,
      animation: { enabled: true, duration: 180 },
      premovable: { enabled: false },
      drawable: {
        enabled: true,
        visible: true,
        brushes: DRAW_BRUSHES,
        onChange: (value) => handlers.current.onDrawnShapesChange?.(value),
      },
      movable: {
        free: false,
        events: {
          after: (from, to) => {
            const finishMove = measureTempoOperation("move-to-paint");
            const chess = positionRef.current.chess;
            const capture =
              Boolean(chess.get(to as Square)) ||
              (chess.get(from as Square)?.type === "p" && from[0] !== to[0]);
            playMoveSound(false, capture);
            apiRef.current?.setAutoShapes([]);
            if (handlers.current.editMode)
              handlers.current.onFreeMove?.(from as Square, to as Square);
            else handlers.current.onMove(from as Square, to as Square);
            requestAnimationFrame(finishMove);
          },
        },
      },
      events: {
        select: (square) => {
          if (handlers.current.editMode)
            handlers.current.onSquareSelect?.(square as Square);
        },
      },
    });
    const frame = requestAnimationFrame(finishReady);
    return () => {
      cancelAnimationFrame(frame);
      apiRef.current?.destroy();
      apiRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    apiRef.current?.set({
      fen,
      orientation: visualOrientation,
      turnColor: position.chess.turn() === "w" ? "white" : "black",
      lastMove: lastMove ? ([...lastMove] as Key[]) : undefined,
      movable: {
        free: editMode,
        color: editMode
          ? "both"
          : locked
            ? undefined
            : position.chess.turn() === "w"
              ? "white"
              : "black",
        dests: editMode ? new Map() : position.destinations,
        showDests: true,
      },
      draggable: { enabled: editMode || !locked, showGhost: true },
      selectable: { enabled: editMode || !locked },
    });
  }, [
    fen,
    visualOrientation,
    position,
    lastMove,
    locked,
    editMode,
    positionRevision,
  ]);

  useEffect(() => {
    apiRef.current?.setAutoShapes(hint ? [...shapes, hint] : shapes);
  }, [shapes, hint]);
  useEffect(() => {
    apiRef.current?.setShapes(drawnShapes);
  }, [drawnShapes]);
  useLayoutEffect(() => {
    if (!surfaceSize) return;
    apiRef.current?.redrawAll();
    if (process.env.NODE_ENV !== "production") {
      const surface = surfaceRef.current?.getBoundingClientRect();
      const board = surfaceRef.current
        ?.querySelector("cg-board")
        ?.getBoundingClientRect();
      if (
        surface &&
        board &&
        [
          Math.abs(surface.width - board.width),
          Math.abs(surface.height - board.height),
          Math.abs(surface.x - board.x),
          Math.abs(surface.y - board.y),
        ].some((delta) => delta > 0.5)
      ) {
        console.error("Tempo board geometry mismatch", { surface, board });
      }
    }
  }, [surfaceSize, theme, pieceSet]);

  return (
    <div className="board-viewport" ref={hostRef}>
      <div
        className="board-frame"
        aria-label="Interactive chessboard"
        data-fen={fen}
        data-orientation={visualOrientation}
        data-hint={Boolean(hint)}
        data-input-enabled={editMode || !locked}
        style={
          surfaceSize
            ? { width: surfaceSize + 18, height: surfaceSize + 18 }
            : undefined
        }
      >
        <div
          className={`chessground-shell theme-${theme} pieces-${pieceSet}`}
          style={
            surfaceSize
              ? { width: surfaceSize, height: surfaceSize }
              : undefined
          }
        >
          <div className="cg-wrap" ref={surfaceRef} />
        </div>
      </div>
    </div>
  );
}
