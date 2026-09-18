"use client";

import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Square } from "chess.js";
import { useShallow } from "zustand/react/shallow";
import { Chessboard } from "./chessboard";
import { useBoardShellStore } from "../state/board-shell-store";

const EMPTY_SHAPES: DrawShape[] = [];

function noopMove(_from: Square, _to: Square) {
  return;
}

export function PersistentBoardShell() {
  const board = useBoardShellStore(useShallow((state) => state.board));
  return (
    <div className="persistent-board-shell" data-board-owner={board.owner}>
      <Chessboard
        fen={board.fen}
        expectedSan={board.expectedSan}
        lastMove={board.lastMove}
        locked={board.interactionMode === "readonly"}
        showHint={board.showHint}
        theme={board.theme}
        pieceSet={board.pieceSet}
        shapes={board.shapes ?? EMPTY_SHAPES}
        drawnShapes={board.drawnShapes ?? EMPTY_SHAPES}
        onDrawnShapesChange={board.onDrawnShapesChange}
        editMode={board.interactionMode === "free"}
        onSquareSelect={board.onSquareSelect}
        onFreeMove={board.onFreeMove}
        onMove={board.onMove ?? noopMove}
        orientation={board.orientation}
        onFlip={board.onFlip}
        positionRevision={board.positionRevision}
      />
    </div>
  );
}
