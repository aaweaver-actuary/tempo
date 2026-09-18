import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Square } from "chess.js";
import { create } from "zustand";
import type { View } from "../types";
import type { BoardTheme, PieceSet } from "../components/chessboard";
import { STANDARD_FEN } from "../const";

export type BoardShellOwner = View | "modal-tree" | "modal-editor";
export type BoardInteractionMode = "readonly" | "legal" | "free";

export type BoardShellSnapshot = {
  owner: BoardShellOwner;
  fen: string;
  expectedSan?: string;
  lastMove?: readonly [string, string];
  orientation: "white" | "black";
  interactionMode: BoardInteractionMode;
  showHint: boolean;
  theme: BoardTheme;
  pieceSet: PieceSet;
  shapes: DrawShape[];
  drawnShapes: DrawShape[];
  positionRevision: number;
  onMove?: (from: Square, to: Square) => void;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onDrawnShapesChange?: (shapes: DrawShape[]) => void;
  onFlip?: () => void;
};

type BoardShellStore = {
  board: BoardShellSnapshot;
  setShellBoardForOwner: (
    owner: BoardShellOwner,
    board: Partial<BoardShellSnapshot>,
  ) => void;
  updateShellBoardForOwner: (
    owner: BoardShellOwner,
    board: Partial<BoardShellSnapshot>,
  ) => void;
  releaseShellBoardForOwner: (owner: BoardShellOwner) => void;
};

const defaultBoardState: BoardShellSnapshot = {
  owner: "train",
  fen: STANDARD_FEN,
  orientation: "white",
  interactionMode: "readonly",
  showHint: false,
  theme: "brown",
  pieceSet: "cburnett",
  shapes: [],
  drawnShapes: [],
  positionRevision: 0,
};

export const useBoardShellStore = create<BoardShellStore>((set) => ({
  board: defaultBoardState,
  setShellBoardForOwner: (owner, board) =>
    set((current) => ({
      board: {
        ...current.board,
        ...board,
        owner,
      },
    })),
  updateShellBoardForOwner: (owner, board) =>
    set((current) => {
      if (current.board.owner !== owner) return current;
      return {
        board: {
          ...current.board,
          ...board,
          owner,
        },
      };
    }),
  releaseShellBoardForOwner: (owner) =>
    set((current) => {
      if (current.board.owner !== owner) return current;
      return { board: { ...defaultBoardState } };
    }),
}));
