"use client";

import type { DrawShape } from "@lichess-org/chessground/draw";
import { Chess, type Square } from "chess.js";
import { createPortal } from "react-dom";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Dialog } from "../dialog";
import { Button } from "../buttons/BaseButton";
import { useBoardKeyboard, useLetterShortcutsEnabled, type BoardKeyboardActions } from "../../lib/keyboard-shortcuts";
import { recordBoardEvent } from "../../lib/board-diagnostics";
import { installTempoDragCapture, measureTempoOperation, measureTempoDragPhase } from "../../lib/performance";
import { playChessMoveSound, playMoveSound } from "../../lib/move-sound";

import { Board, type BoardHandle, type BoardShape, type BoardTheme, type PieceSet, type Square as BoardSquare } from "@tempo/board-ui";
import "@tempo/board-ui/styles.css";
export type { BoardTheme, PieceSet } from "@tempo/board-ui";
export type PromotionPiece = "q" | "r" | "b" | "n";
const EMPTY_SHAPES: DrawShape[] = [];
function boardSquare(value: string | undefined): BoardSquare | undefined {
  return value && /^[a-h][1-8]$/.test(value) ? value as BoardSquare : undefined;
}

// Strip renderer-only data, retaining the package's declared display fields.
function neutralShapes(shapes: readonly DrawShape[]): BoardShape[] {
  return shapes.flatMap(shape => {
    const orig = boardSquare(shape.orig), dest = boardSquare(shape.dest);
    if (!orig || (shape.dest && !dest)) return [];
    const brush = ["green", "red", "blue", "yellow", "maia"].includes(shape.brush ?? "")
      ? shape.brush as BoardShape["brush"] : undefined;
    return [{ orig, ...(dest ? { dest } : {}), ...(brush ? { brush } : {}),
      ...(shape.below !== undefined ? { below: shape.below } : {}),
      ...(shape.modifiers ? { modifiers: { ...shape.modifiers } } : {}),
      ...(shape.piece ? { piece: { ...shape.piece } } : {}),
      ...(shape.label ? { label: { ...shape.label } } : {}) }];
  });
}

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
  const keyboardContainerRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const boardRef = useRef<BoardHandle>(null);
  const captureRef = useRef<ReturnType<typeof installTempoDragCapture> | null>(null);
  const [movedPosition, setMovedPosition] = useState("");
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
  const [preparedHint, setPreparedHint] = useState<{
    fen: string; san: string; shape?: BoardShape;
  }>();

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
  const revision = positionRevision;
  const positionToken = JSON.stringify([positionIdentity, positionRevision, fen, visualOrientation, editMode, selectOnly, locked]);
  const previousPositionToken = useRef(positionToken);
  useLayoutEffect(() => {
    if (previousPositionToken.current !== positionToken) {
      previousPositionToken.current = positionToken;
      setMovedPosition("");
    }
  }, [positionToken]);
  useEffect(() => { if (hint) onHintExposure?.(fen); }, [hint, fen, onHintExposure]);
  useLayoutEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const finishReady = measureTempoOperation("board-ready");
    const frame = requestAnimationFrame(finishReady);
    captureRef.current = installTempoDragCapture(surface, () => boardRef.current?.readInputSnapshot() ?? null);
    return () => { cancelAnimationFrame(frame); captureRef.current?.dispose(); captureRef.current = null; };
  }, []);
  function move(from: BoardSquare, to: BoardSquare) {
    if (locked || selectOnly) return;
    const finishMove = measureTempoOperation("move-to-paint");
    const finishDrop = measureTempoDragPhase("drop-handling");
    if (editMode) {
      const capture = Boolean(position.chess.get(to)) ||
        (position.chess.get(from)?.type === "p" && from[0] !== to[0]);
      playMoveSound({ capture });
      onFreeMove?.(from, to);
    } else {
      try {
        const next = new Chess(position.chess.fen());
        const played = next.move({ from, to, promotion });
        playChessMoveSound(played, next.isCheck());
      } catch { playMoveSound({ illegal: true }); }
      onMove(from, to);
    }
    setMovedPosition(positionToken);
    finishDrop(); requestAnimationFrame(finishMove);
  }
  const displayShapes = movedPosition === positionToken ? [] : neutralShapes(hint ? [...shapes, hint as DrawShape] : shapes);
  const last = lastMove && boardSquare(lastMove[0]) && boardSquare(lastMove[1])
    ? [boardSquare(lastMove[0])!, boardSquare(lastMove[1])!] as const : undefined;

  return (
    <div className="board-keyboard-container" ref={keyboardContainerRef}>
    <div className="board-viewport">
      <div className="board-frame" ref={surfaceRef} data-fen={fen} data-orientation={visualOrientation}
        data-hint={Boolean(hint)} data-input-enabled={!locked}>
        <Board ref={boardRef} fen={fen} positionIdentity={positionIdentity} positionRevision={revision}
          orientation={visualOrientation} turn={position.chess.turn() === "w" ? "white" : "black"}
          check={position.chess.isCheck() ? (position.chess.turn() === "w" ? "white" : "black") : false}
          mode={selectOnly ? "select" : editMode ? "free" : "legal"} locked={locked}
          destinations={position.destinations} theme={theme} pieceSet={pieceSet} lastMove={last}
          shapes={displayShapes} drawnShapes={neutralShapes(drawnShapes)} layoutAnchor={layoutAnchor}
          onMove={({ from, to }) => move(from, to)} onFreeMove={({ from, to }) => move(from, to)}
          onSelect={({ square }) => onSquareSelect?.(square)}
          onDrawingChange={({ shapes: changed }) => onDrawnShapesChange?.(changed.map(shape => ({ ...shape })) as DrawShape[])}
          onDiagnosticEvent={({ kind, changed }) => {
            captureRef.current?.boardEvent(kind, [...changed]);
            if (kind === "cancel-move") { recordBoardEvent("inputCancellations"); recordBoardEvent("positionResets"); }
          }} />
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
