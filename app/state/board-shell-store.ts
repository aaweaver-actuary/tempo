import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Square } from "chess.js";
import { create } from "zustand";
import type { View } from "../types";
import type { BoardTheme, PieceSet } from "../components/chessboard";
import { STANDARD_FEN } from "../const";
import { recordBoardEvent } from "../lib/board-diagnostics";

export type BoardShellOwner = View | "modal-tree" | "modal-editor";
export type BoardInteractionMode = "readonly" | "legal" | "free" | "select";

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
  positionKey?: string;
  unavailable?: string;
  onMove?: (from: Square, to: Square) => void;
  onSquareSelect?: (square: Square) => void;
  onFreeMove?: (from: Square, to: Square) => void;
  onDrawnShapesChange?: (shapes: DrawShape[]) => void;
  onFlip?: () => void;
};

type BoardShellStore = {
  session: number;
  leaseActive: boolean;
  acquireShellBoardForOwner: (owner: BoardShellOwner, board: BoardShellSnapshot) => number;
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

export function sameLastMove(left?: readonly [string, string], right?: readonly [string, string]) {
  return left === right || Boolean(left && right && left[0] === right[0] && left[1] === right[1]);
}

export function sameBoardShapes(left: DrawShape[], right: DrawShape[]) {
  return left === right || (left.length === right.length && left.every((shape, index) => {
    const other = right[index];
    return shape.orig === other.orig && shape.dest === other.dest && shape.brush === other.brush &&
      shape.below === other.below && shape.modifiers?.lineWidth === other.modifiers?.lineWidth &&
      shape.modifiers?.hilite === other.modifiers?.hilite && shape.piece?.role === other.piece?.role &&
      shape.piece?.color === other.piece?.color && shape.piece?.scale === other.piece?.scale &&
      shape.customSvg?.html === other.customSvg?.html && shape.customSvg?.center === other.customSvg?.center &&
      shape.label?.text === other.label?.text && shape.label?.fill === other.label?.fill;
  }));
}

function equivalentSnapshot(left: BoardShellSnapshot, right: BoardShellSnapshot) {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)] as (keyof BoardShellSnapshot)[]);
  return [...keys].every((key) => key === "lastMove" ? sameLastMove(left.lastMove, right.lastMove)
    : key === "shapes" || key === "drawnShapes" ? sameBoardShapes(left[key], right[key])
    : left[key] === right[key]);
}

function publishSnapshot(current: BoardShellStore, board: BoardShellSnapshot) {
  if (equivalentSnapshot(current.board, board)) {
    recordBoardEvent("equivalentPublications");
    return current;
  }
  recordBoardEvent("publications");
  return { board };
}

export const useBoardShellStore = create<BoardShellStore>((set) => ({
  session: 0,
  leaseActive: false,
  board: defaultBoardState,
  acquireShellBoardForOwner: (owner, board) => {
    let acquiredSession = 0;
    set((current) => {
      acquiredSession = current.session + 1;
      recordBoardEvent("acquisitions");
      recordBoardEvent("publications");
      return { session: acquiredSession, leaseActive: true, board: { ...defaultBoardState, ...board, owner } };
    });
    return acquiredSession;
  },
  // Compatibility for direct store consumers. Mounted publishers use explicit
  // acquisition and exact-session updates; an update cannot reacquire a lease.
  setShellBoardForOwner: (owner, board, session) =>
    set((current) => {
      if (session !== undefined && (session < current.session ||
        (session === current.session && (!current.leaseActive || current.board.owner !== owner)))) {
        recordBoardEvent("rejectedPublications");
        return current;
      }
      const completeSnapshot = { ...defaultBoardState, ...board, owner };
      if (session === undefined || session > current.session) {
        recordBoardEvent("acquisitions");
        recordBoardEvent("publications");
        return { session: session ?? current.session + 1, leaseActive: true, board: completeSnapshot };
      }
      return publishSnapshot(current, completeSnapshot);
    }),
  updateShellBoardForOwner: (owner, board, session) =>
    set((current) => {
      if (!current.leaseActive || current.board.owner !== owner ||
        (session !== undefined && session !== current.session)) {
        recordBoardEvent("rejectedPublications");
        return current;
      }
      return publishSnapshot(current, { ...current.board, ...board, owner });
    }),
  releaseShellBoardForOwner: (owner, session) =>
    set((current) => {
      if (!current.leaseActive || current.board.owner !== owner ||
        (session !== undefined && session !== current.session)) return current;
      recordBoardEvent("releases");
      return { leaseActive: false, board: { ...defaultBoardState } };
    }),
}));
