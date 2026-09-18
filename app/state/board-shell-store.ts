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
  unavailable?: string;
  onMove?: (from: Square, to: Square) => void;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onDrawnShapesChange?: (shapes: DrawShape[]) => void;
  onFlip?: () => void;
};

type BoardShellStore = {
  session: number;
  board: BoardShellSnapshot;
  setShellBoardForOwner: (
    owner: BoardShellOwner,
    board: BoardShellSnapshot,
    session?: number,
  ) => void;
  updateShellBoardForOwner: (
    owner: BoardShellOwner,
    board: Partial<BoardShellSnapshot>,
    session?: number,
  ) => void;
  releaseShellBoardForOwner: (owner: BoardShellOwner, session?: number) => void;
};

export const defaultBoardState: BoardShellSnapshot = {
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
  session: 0,
  board: defaultBoardState,
  setShellBoardForOwner: (owner, board, session) =>
    set((current) => {
      if (session !== undefined && session < current.session) return current;
      return {
        session: session ?? current.session,
        board: { ...defaultBoardState, ...board, owner },
      };
    }),
  updateShellBoardForOwner: (owner, board, session) =>
    set((current) => {
      if (
        current.board.owner !== owner ||
        (session !== undefined && session !== current.session)
      )
        return current;
      return {
        board: {
          ...current.board,
          ...board,
          owner,
        },
      };
    }),
  releaseShellBoardForOwner: (owner, session) =>
    set((current) => {
      if (
        current.board.owner !== owner ||
        (session !== undefined && session !== current.session)
      )
        return current;
      return { board: { ...defaultBoardState } };
    }),
}));
