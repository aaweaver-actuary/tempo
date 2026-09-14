'use client';

import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, type Move, type Square } from 'chess.js';
import { useEffect, useMemo, useRef } from 'react';

export type BoardTheme = 'brown' | 'blue' | 'green';
export type PieceSet = 'cburnett' | 'merida';

type ChessboardProps = {
  fen: string;
  expectedSan?: string;
  lastMove?: [string, string];
  locked: boolean;
  showHint: boolean;
  theme: BoardTheme;
  pieceSet: PieceSet;
  shapes?: DrawShape[];
  editMode?: boolean;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onMove: (from: Square, to: Square) => void;
};

function moveForSan(chess: Chess, san: string): Move | undefined {
  return chess.moves({ verbose: true }).find((move) => move.san === san);
}

export function Chessboard({ fen, expectedSan, lastMove, locked, showHint, theme, pieceSet, shapes = [], editMode = false, onSquareSelect, onFreeMove, onMove }: ChessboardProps) {
  const elementRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const onMoveRef = useRef(onMove);
  const onFreeMoveRef = useRef(onFreeMove);
  const onSquareSelectRef = useRef(onSquareSelect);
  const chess = useMemo(() => { try { return new Chess(fen); } catch { return new Chess(); } }, [fen]);
  const hintMove = expectedSan ? moveForSan(chess, expectedSan) : undefined;

  useEffect(() => {
    onMoveRef.current = onMove;
    onFreeMoveRef.current = onFreeMove;
    onSquareSelectRef.current = onSquareSelect;
  }, [onFreeMove, onMove, onSquareSelect]);

  useEffect(() => {
    if (!elementRef.current) return;
    apiRef.current = Chessground(elementRef.current);
    return () => { apiRef.current?.destroy(); apiRef.current = null; };
  }, []);

  useEffect(() => {
    const destinations = new Map<Key, Key[]>();
    for (const move of chess.moves({ verbose: true })) {
      const from = move.from as Key;
      destinations.set(from, [...(destinations.get(from) ?? []), move.to as Key]);
    }
    const autoShapes: DrawShape[] = [
      ...shapes,
      ...(showHint && hintMove ? [{ orig: hintMove.from as Key, dest: hintMove.to as Key, brush: 'yellow' }] : []),
    ];
    apiRef.current?.set({
      fen,
      orientation: 'white',
      turnColor: chess.turn() === 'w' ? 'white' : 'black',
      lastMove: lastMove as Key[] | undefined,
      coordinates: true,
      viewOnly: locked && !editMode,
      animation: { enabled: true, duration: 180 },
      movable: {
        free: editMode,
        color: editMode ? 'both' : locked ? undefined : chess.turn() === 'w' ? 'white' : 'black',
        dests: editMode ? undefined : destinations,
        showDests: true,
        events: { after: (from, to) => editMode ? onFreeMoveRef.current?.(from as Square, to as Square) : onMoveRef.current(from as Square, to as Square) },
      },
      draggable: { enabled: editMode || !locked, showGhost: true },
      selectable: { enabled: editMode || !locked },
      events: { select: (square) => { if (editMode) onSquareSelectRef.current?.(square as Square); } },
      drawable: {
        enabled: true,
        visible: true,
        autoShapes,
        brushes: {
          green: { key: 'g', color: '#4f8a59', opacity: .88, lineWidth: 10 },
          red: { key: 'r', color: '#b45f50', opacity: .88, lineWidth: 10 },
          blue: { key: 'b', color: '#4e7ca8', opacity: .88, lineWidth: 10 },
          yellow: { key: 'y', color: '#d0a83f', opacity: .92, lineWidth: 11 },
          maia: { key: 'm', color: '#8a62a5', opacity: .9, lineWidth: 10 },
        },
      },
    });
  }, [chess, editMode, fen, hintMove, lastMove, locked, shapes, showHint]);

  return (
    <div className="board-frame" aria-label="Interactive chessboard" data-fen={fen}>
      <div className={`chessground-shell theme-${theme} pieces-${pieceSet}`}>
        <div className="cg-wrap" ref={elementRef}/>
      </div>
    </div>
  );
}
