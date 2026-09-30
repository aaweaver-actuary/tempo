"use client";

import { Chessground } from "@lichess-org/chessground";
import type { Api } from "@lichess-org/chessground/api";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { Chess, type Square } from "chess.js";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useBoardViewport } from "../../hooks/use-board-viewport";
import { sameLastMove, sameBoardShapes } from "../../state/board-shell-store";
import { recordBoardEvent } from "../../lib/board-diagnostics";
import { measureTempoOperation } from "../../lib/performance";
import { playChessMoveSound, playMoveSound } from "../../lib/move-sound";

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
  owner?: string;
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
  selectOnly?: boolean;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onMove: (from: Square, to: Square) => void;
  orientation?: "white" | "black";
  onFlip?: () => void;
  positionRevision?: number;
  positionKey?: string;
};

export function Chessboard({
  fen,
  owner,
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
  selectOnly = false,
  onSquareSelect,
  onFreeMove,
  onMove,
  orientation = "white",
  onFlip,
  positionRevision = 0,
  positionKey,
}: ChessboardProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const appliedPosition = useRef<{
    fen: string; owner?: string; positionKey?: string; positionRevision: number;
    orientation: "white" | "black"; locked: boolean; editMode: boolean; selectOnly: boolean;
    lastMove?: readonly [string, string];
  } | undefined>(undefined);
  const resetVersion = useRef(0);
  const inputGeneration = useRef(0);
  const appliedAutoShapes = useRef<{ shapes: DrawShape[]; version: number } | undefined>(undefined);
  const appliedDrawnShapes = useRef<{ shapes: DrawShape[]; version: number } | undefined>(undefined);
  const handlers = useRef({
    onMove,
    onFreeMove,
    onSquareSelect,
    onDrawnShapesChange,
    editMode,
    selectOnly,
    locked,
  });
  const surfaceSize = useBoardViewport(hostRef);
  const [flippedOwner, setFlippedOwner] = useState<string | null>(null);
  const flipped = flippedOwner === (owner ?? "embedded");
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
      selectOnly,
      locked,
    };
    positionRef.current = position;
  }, [
    onMove,
    onFreeMove,
    onSquareSelect,
    onDrawnShapesChange,
    editMode,
    selectOnly,
    locked,
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
    const flip = (event: Event) => {
      if (
        !surfaceRef.current?.getClientRects().length ||
        hostRef.current?.closest('[data-unavailable="true"]')
      )
        return;
      const target = event.target as HTMLElement | null;
      if (
        event instanceof KeyboardEvent &&
        (event.key.toLowerCase() !== "f" ||
          target?.matches('input,textarea,select,[contenteditable="true"]'))
      )
        return;
      if (
        document.querySelector('[role="dialog"]') &&
        !surfaceRef.current?.closest('[role="dialog"]')
      )
        return;
      event.preventDefault();
      if (onFlip) onFlip();
      else
        setFlippedOwner((current) =>
          current === (owner ?? "embedded") ? null : (owner ?? "embedded"),
        );
    };
    window.addEventListener("keydown", flip);
    window.addEventListener("tempo:flip-board", flip);
    return () => {
      window.removeEventListener("keydown", flip);
      window.removeEventListener("tempo:flip-board", flip);
    };
  }, [onFlip, owner]);

  // Chessground defers events by a timer. A callback queued before genuine
  // invalidation must not grade a replacement card or change its annotations.
  const createInputEvents = useCallback(() => {
    const generation = inputGeneration.current;
    return {
      after: (from: Key, to: Key) => {
        if (generation !== inputGeneration.current) return;
        if (handlers.current.locked || handlers.current.selectOnly) return;
        const finishMove = measureTempoOperation("move-to-paint");
        const chess = positionRef.current.chess;
        if (handlers.current.editMode) {
          const capture =
            Boolean(chess.get(to as Square)) ||
            (chess.get(from as Square)?.type === "p" && from[0] !== to[0]);
          playMoveSound({ capture });
        } else {
          try {
            const nextPosition = new Chess(chess.fen());
            const move = nextPosition.move({ from, to, promotion: "q" });
            playChessMoveSound(move, nextPosition.isCheck());
          } catch {
            playMoveSound();
          }
        }
        apiRef.current?.setAutoShapes([]);
        if (handlers.current.editMode)
          handlers.current.onFreeMove?.(from as Square, to as Square);
        else handlers.current.onMove(from as Square, to as Square);
        requestAnimationFrame(finishMove);
      },
      select: (square: Key) => {
        if (generation !== inputGeneration.current) return;
        if (!handlers.current.locked && (handlers.current.editMode || handlers.current.selectOnly))
          handlers.current.onSquareSelect?.(square as Square);
      },
      onDrawnShapesChange: (value: DrawShape[]) => {
        if (generation === inputGeneration.current) handlers.current.onDrawnShapesChange?.(value);
      },
    };
  }, []);

  useLayoutEffect(() => {
    if (!surfaceRef.current) return;
    const inputEvents = createInputEvents();
    const finishReady = measureTempoOperation("board-ready");
    apiRef.current = Chessground(surfaceRef.current, {
      viewOnly: false,
      coordinates: true,
      animation: {
        enabled: !window.matchMedia?.("(prefers-reduced-motion: reduce)")
          .matches,
        duration: 180,
      },
      premovable: { enabled: false },
      drawable: {
        enabled: true,
        visible: true,
        brushes: DRAW_BRUSHES,
        onChange: inputEvents.onDrawnShapesChange,
      },
      movable: {
        free: false,
        events: {
          after: inputEvents.after,
        },
      },
      events: {
        select: inputEvents.select,
      },
    });
    const frame = requestAnimationFrame(finishReady);
    return () => {
      cancelAnimationFrame(frame);
      apiRef.current?.destroy();
      apiRef.current = null;
      inputGeneration.current += 1;
      appliedPosition.current = undefined;
      appliedAutoShapes.current = undefined;
      appliedDrawnShapes.current = undefined;
    };
  }, [createInputEvents]);

  useLayoutEffect(() => {
    const configuration: Parameters<Api["set"]>[0] = {};
    const previousPosition = appliedPosition.current;
    const positionChanged = !previousPosition || previousPosition.fen !== fen || previousPosition.owner !== owner ||
      previousPosition.positionKey !== positionKey || previousPosition.positionRevision !== positionRevision;
    const modeChanged = !previousPosition || previousPosition.editMode !== editMode || previousPosition.selectOnly !== selectOnly;
    const lockChanged = !previousPosition || previousPosition.locked !== locked;
    const orientationChanged = !previousPosition || previousPosition.orientation !== visualOrientation;
    const highlightChanged = !previousPosition || !sameLastMove(previousPosition.lastMove, lastMove);
    const becameLocked = previousPosition !== undefined && !previousPosition.locked && locked;
    // Chessground applies a user move before its deferred callback. Cancelling
    // input cannot undo that move; genuine invalidation restores current props.
    const mustRestoreAuthoritativePosition = positionChanged || modeChanged || becameLocked || orientationChanged;
    const mustRefreshInputConfiguration = mustRestoreAuthoritativePosition || lockChanged;
    if (mustRestoreAuthoritativePosition) {
      recordBoardEvent("inputCancellations");
      apiRef.current?.cancelMove?.();
    }
    if (mustRestoreAuthoritativePosition) {
      recordBoardEvent("positionResets");
      resetVersion.current += 1;
      Object.assign(configuration, {
        fen,
        check: position.chess.isCheck()
          ? position.chess.turn() === "w" ? "white" : "black"
          : false,
        turnColor: position.chess.turn() === "w" ? "white" : "black",
      });
    }
    if (mustRefreshInputConfiguration) {
      Object.assign(configuration, {
        movable: {
          free: editMode,
          color: locked || selectOnly ? undefined : editMode ? "both"
            : position.chess.turn() === "w" ? "white" : "black",
          dests: editMode ? new Map() : position.destinations,
          showDests: true,
        },
        draggable: { enabled: !locked && !selectOnly, showGhost: true },
        selectable: { enabled: !locked },
      });
    }
    if (previousPosition && mustRefreshInputConfiguration) {
      inputGeneration.current += 1;
      const inputEvents = createInputEvents();
      configuration.movable = { ...configuration.movable, events: { after: inputEvents.after } };
      configuration.events = { select: inputEvents.select };
      configuration.drawable = { onChange: inputEvents.onDrawnShapesChange };
    }
    if (orientationChanged) Object.assign(configuration, { orientation: visualOrientation });
    if (mustRestoreAuthoritativePosition || highlightChanged)
      Object.assign(configuration, { lastMove: lastMove ? [...lastMove] as Key[] : undefined });
    if (Object.keys(configuration).length) apiRef.current?.set(configuration);
    appliedPosition.current = { fen, owner, positionKey, positionRevision, orientation: visualOrientation,
      locked, editMode, selectOnly, lastMove };
  });

  useLayoutEffect(() => {
    const autoShapes = hint ? [...shapes, hint] : shapes;
    if (appliedAutoShapes.current?.version === resetVersion.current &&
      sameBoardShapes(appliedAutoShapes.current.shapes, autoShapes)) return;
    // Chessground may mutate drawing arrays; keep published snapshots immutable.
    apiRef.current?.setAutoShapes([...autoShapes]);
    appliedAutoShapes.current = { shapes: autoShapes, version: resetVersion.current };
  });
  useLayoutEffect(() => {
    if (appliedDrawnShapes.current?.version === resetVersion.current &&
      sameBoardShapes(appliedDrawnShapes.current.shapes, drawnShapes)) return;
    apiRef.current?.setShapes([...drawnShapes]);
    appliedDrawnShapes.current = { shapes: drawnShapes, version: resetVersion.current };
  });
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
  }, [surfaceSize]);

  return (
    <div className="board-viewport" ref={hostRef}>
      <div
        className="board-frame"
        aria-label="Interactive chessboard"
        data-fen={fen}
        data-orientation={visualOrientation}
        data-hint={Boolean(hint)}
        data-input-enabled={!locked}
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
