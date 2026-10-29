"use client";

import { Board, type BoardHandle, type BoardRendererEvent, type BoardShape, type BoardTheme, type PieceSet, type Square as BoardSquare } from "@tempo/board-ui";
import type { DrawShape } from "@lichess-org/chessground/draw";
import { Chess, type Square } from "chess.js";
import { createPortal } from "react-dom";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Dialog } from "../dialog";
import { Button } from "../buttons/BaseButton";
import { useBoardKeyboard, useLetterShortcutsEnabled, type BoardKeyboardActions } from "../../lib/keyboard-shortcuts";
import { useBoardViewport } from "../../hooks/use-board-viewport";
import { recordBoardEvent } from "../../lib/board-diagnostics";
import { sameBoardShapes } from "../../state/board-shell-store";
import { installTempoDragCapture, measureTempoOperation, measureTempoDragPhase } from "../../lib/performance";
import { playChessMoveSound, playMoveSound } from "../../lib/move-sound";

export type { BoardTheme, PieceSet } from "@tempo/board-ui";
export type PromotionPiece = "q" | "r" | "b" | "n";
const EMPTY_SHAPES: DrawShape[] = [];

type ChessboardProps = {
  showShortcutButton?: boolean;
  layoutAnchor?: HTMLElement | null;
  keyboard?: BoardKeyboardActions;
  owner?: string;
  fen: string;
  promotion?: PromotionPiece;
  expectedSan?: string;
  lastMove?: readonly [string, string];
  locked: boolean;
  showHint: boolean;
  onHintExposure?: (fen: string) => void;
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
  showShortcutButton = true,
  layoutAnchor,
  fen,
  promotion = "q",
  keyboard,
  owner,
  expectedSan,
  lastMove,
  locked,
  showHint,
  onHintExposure,
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
  const keyboardContainerRef = useRef<HTMLDivElement>(null);
  const boardRef = useRef<BoardHandle>(null);
  const readyFrame = useRef(0);
  const captureRef = useRef<ReturnType<typeof installTempoDragCapture> | null>(null);
  const readyMeasurement = useRef<ReturnType<typeof measureTempoOperation> | null>(null);
  if (readyMeasurement.current === null) { readyMeasurement.current = measureTempoOperation("board-ready"); }
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
    const destinations = new Map<BoardSquare, BoardSquare[]>();
    for (const move of legalMoves) {
      const from = move.from as BoardSquare;
      const targets = destinations.get(from) ?? [];
      if (!targets.includes(move.to as BoardSquare)) targets.push(move.to as BoardSquare);
      destinations.set(from, targets);
    }
    return { chess, legalMoves, destinations };
  }, [fen, editMode]);
  const [preparedHint, setPreparedHint] = useState<{ fen: string; san: string; shape?: DrawShape }>();
  useEffect(() => {
    if (!showHint || !expectedSan) return;
    const timer = window.setTimeout(() => {
      const move = position.legalMoves.find((move) => move.san === expectedSan);
      setPreparedHint({
        fen,
        san: expectedSan,
        shape: move
          ? { orig: move.from as BoardSquare, dest: move.to as BoardSquare, brush: "yellow" }
          : undefined,
      });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [position, expectedSan, fen, showHint]);
  const hint =
    showHint && preparedHint?.fen === fen && preparedHint.san === expectedSan
      ? preparedHint.shape
      : undefined;

  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false);
  const shortcutTitleId = useId();
  const [keyboardResetRevision, setKeyboardResetRevision] = useState(0);
  const letterKeysEnabled = useLetterShortcutsEnabled();
  const flipBoard = () => {
    if (onFlip) onFlip();
    else setFlippedOwner(current => current === (owner ?? "embedded") ? null : (owner ?? "embedded"));
  };
  const resetBoard = () => {
    setFlippedOwner(null);
    setKeyboardResetRevision(revision => revision + 1);
    keyboard?.reset?.();
  };
  useBoardKeyboard(keyboardContainerRef, { ...keyboard, flip: flipBoard, reset: resetBoard, help: () => setShortcutHelpOpen(true) });

  const positionIdentity = JSON.stringify([owner ?? "embedded", positionKey ?? "", keyboardResetRevision]);
  const [clearedAnnotations, setClearedAnnotations] = useState<{ identity: string; shapes: DrawShape[]; hint?: DrawShape } | null>(null);
  const annotationIdentity = JSON.stringify([positionIdentity, positionRevision, fen]);
  const annotationsCleared = clearedAnnotations?.identity === annotationIdentity &&
    sameBoardShapes(clearedAnnotations.shapes, shapes) && clearedAnnotations.hint === hint;
  const autoShapes = annotationsCleared ? EMPTY_SHAPES : hint ? [...shapes, hint] : shapes;

  function handleMove(from: Square, to: Square, free: boolean) {
    const finishMove = measureTempoOperation("move-to-paint");
    const finishDrop = measureTempoDragPhase("drop-handling");
    if (free) {
      const capture = Boolean(position.chess.get(to)) || (position.chess.get(from)?.type === "p" && from[0] !== to[0]);
      playMoveSound({ capture });
    } else {
      try {
        const nextPosition = new Chess(fen);
        const move = nextPosition.move({ from, to, promotion });
        playChessMoveSound(move, nextPosition.isCheck());
      } catch { playMoveSound(); }
    }
    setClearedAnnotations({ identity: annotationIdentity, shapes, hint });
    if (free) onFreeMove?.(from, to);
    else onMove(from, to);
    finishDrop();
    requestAnimationFrame(finishMove);
  }

  function rendererEvent(event: BoardRendererEvent) {
    if (event.type === "reset") {
      recordBoardEvent("inputCancellations");
      recordBoardEvent("positionResets");
      captureRef.current?.boardEvent("cancel-move", [...event.changed]);
    } else if (event.type === "update") captureRef.current?.boardEvent("set", [...event.changed]);
    else if (event.type === "annotations") captureRef.current?.boardEvent("annotations");
    else if (event.type === "resize") captureRef.current?.boardEvent("redraw");
    else if (event.type === "ready") readyFrame.current = requestAnimationFrame(() => readyMeasurement.current?.());
  }

  useLayoutEffect(() => {
    if (!keyboardContainerRef.current) return;
    const capture = installTempoDragCapture(keyboardContainerRef.current, refreshBounds => boardRef.current?.getInteractionSnapshot(refreshBounds) ?? null);
    captureRef.current = capture;
    return () => { cancelAnimationFrame(readyFrame.current); capture.dispose(); captureRef.current = null; };
  }, []);
  useLayoutEffect(() => { if (hint) onHintExposure?.(fen); });

  return (
    <div className="board-keyboard-container" ref={keyboardContainerRef}>
    <div className="board-viewport" ref={hostRef} tabIndex={0}>
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
        <div className="chessground-shell" style={surfaceSize ? { width: surfaceSize, height: surfaceSize } : undefined}>
          <Board ref={boardRef} fen={fen} orientation={visualOrientation}
            turn={position.chess.turn() === "w" ? "white" : "black"}
            check={position.chess.isCheck() ? position.chess.turn() === "w" ? "white" : "black" : false}
            positionIdentity={positionIdentity} positionRevision={positionRevision}
            mode={selectOnly ? "select" : editMode ? "free" : "legal"} locked={locked}
            theme={theme} pieceSet={pieceSet} destinations={position.destinations}
            lastMove={lastMove as readonly [BoardSquare, BoardSquare] | undefined}
            shapes={autoShapes as readonly BoardShape[]} drawnShapes={drawnShapes as readonly BoardShape[]}
            onMove={event => handleMove(event.from, event.to, false)}
            onFreeMove={event => handleMove(event.from, event.to, true)}
            onSelect={event => onSquareSelect?.(event.square)}
            onDrawingChange={event => onDrawnShapesChange?.([...event.shapes])}
            onRendererEvent={rendererEvent} layoutAnchor={layoutAnchor}
            maxSize={surfaceSize || undefined} tabIndex={-1} ariaLabel="Chessboard surface" />
        </div>
      </div>
    </div>
      {showShortcutButton && <div className="board-shortcut-controls"><Button type="button" aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)"
        onClick={event => { event.currentTarget.focus(); setShortcutHelpOpen(true); }}>? Keys</Button></div>}
      {shortcutHelpOpen && createPortal(<Dialog titleId={shortcutTitleId} onClose={() => setShortcutHelpOpen(false)} className="keyboard-help">
        <h2 id={shortcutTitleId}>Keyboard shortcuts</h2>
        <p>Navigation stops at the furthest revealed move. R returns to your decision without restarting.</p>
        <dl>{([
          ["←", "Previous move", Boolean(keyboard?.previous)], ["→", "Next revealed move", Boolean(keyboard?.next)],
          ["↑ / Home", "Beginning of line", Boolean(keyboard?.start)], ["↓ / End", "Furthest revealed position", Boolean(keyboard?.end)],
          ["F", "Flip board", letterKeysEnabled], ["R", "Return to decision and orientation", letterKeysEnabled],
          ["H", "Hint", letterKeysEnabled && Boolean(keyboard?.hint)], ["N", "Next / Continue", letterKeysEnabled && Boolean(keyboard?.nextItem)],
          ["?", "Keyboard shortcuts", true], ["Esc", "Close the topmost popup", true],
        ] as const).map(([key, description, available]) => <div key={key} aria-disabled={!available}><dt><kbd>{key}</kbd></dt><dd>{description}{!available && " (unavailable)"}</dd></div>)}</dl>
        <button type="button" onClick={() => setShortcutHelpOpen(false)}>Close</button>
      </Dialog>, document.body)}
    </div>
  );
}
