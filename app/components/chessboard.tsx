'use client';

import { Chessground } from '@lichess-org/chessground';
import type { Api } from '@lichess-org/chessground/api';
import type { DrawShape } from '@lichess-org/chessground/draw';
import type { Key } from '@lichess-org/chessground/types';
import { Chess, type Move, type Square } from 'chess.js';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { playMoveSound } from '../lib/move-sound';

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
  orientation?: 'white' | 'black';
};

function moveForSan(chess: Chess, san: string): Move | undefined {
  return chess.moves({ verbose: true }).find((move) => move.san === san);
}

export function Chessboard({ fen, expectedSan, lastMove, locked, showHint, theme, pieceSet, shapes = [], editMode = false, onSquareSelect, onFreeMove, onMove, orientation = 'white' }: ChessboardProps) {
  const elementRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<Api | null>(null);
  const onMoveRef = useRef(onMove);
  const onFreeMoveRef = useRef(onFreeMove);
  const onSquareSelectRef = useRef(onSquareSelect);
  const [boardSize, setBoardSize] = useState<number>();
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
      orientation,
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
        events: { after: (from, to) => {
          playMoveSound();
          if (editMode) onFreeMoveRef.current?.(from as Square, to as Square);
          else onMoveRef.current(from as Square, to as Square);
        } },
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
  }, [chess, editMode, fen, hintMove, lastMove, locked, orientation, shapes, showHint]);

  useLayoutEffect(() => {
    const element = elementRef.current?.parentElement?.parentElement;
    const parent = element?.parentElement;
    if (!element || !parent) return;
    const fit = () => {
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const top = element.getBoundingClientRect().top;
      const availableHeight = Math.max(120, viewportHeight - Math.max(0, top) - 12);
      const availableWidth = parent.clientWidth;
      setBoardSize(Math.floor(Math.min(availableWidth, availableHeight, 760)));
    };
    const observer = new ResizeObserver(fit);
    observer.observe(parent);
    window.addEventListener('resize', fit);
    window.visualViewport?.addEventListener('resize', fit);
    requestAnimationFrame(fit);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', fit);
      window.visualViewport?.removeEventListener('resize', fit);
    };
  }, []);

  return (
    <div className="board-frame" aria-label="Interactive chessboard" data-fen={fen} style={boardSize ? { width: boardSize, height: boardSize } : undefined}>
      <div className={`chessground-shell theme-${theme} pieces-${pieceSet}`}>
        <div className="cg-wrap" ref={elementRef}/>
      </div>
    </div>
  );
}
