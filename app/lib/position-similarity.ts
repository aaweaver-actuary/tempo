import { Chess, type Square } from "chess.js";
import type { AnalysisLine } from "../types";

export type IndexedPosition = {
  lineId: string;
  repertoireId: string;
  repertoireName: string;
  fen: string;
  ply: number;
  nextUci?: string;
};

function stateParts(fen: string) {
  const [placement, turn, castling, enPassant] = fen.split(/\s+/);
  return { placement, turn, castling, enPassant };
}

function pieceSquares(board: Chess): Map<string, Set<string>> {
  const groups = new Map<string, Set<string>>();
  for (const row of board.board()) {
    for (const piece of row) {
      if (!piece) continue;
      const key = `${piece.color}${piece.type}`;
      const values = groups.get(key) ?? new Set<string>();
      values.add(piece.square);
      groups.set(key, values);
    }
  }
  return groups;
}

export function prepareChessPositionDistance(leftFen: string): (rightFen: string) => number | undefined {
  let leftState: ReturnType<typeof stateParts>;
  let leftPieces: Map<string, Set<string>>;
  try {
    const leftBoard = new Chess(leftFen);
    leftState = stateParts(leftBoard.fen());
    leftPieces = pieceSquares(leftBoard);
  } catch {
    return () => undefined;
  }
  return (rightFen) => {
    const rightInputState = stateParts(rightFen);
    if (leftState.turn !== rightInputState.turn) return undefined;
    const stateNeedsCanonicalization =
      leftState.castling !== rightInputState.castling ||
      leftState.enPassant !== rightInputState.enPassant;
    let rightBoard: Chess;
    try {
      rightBoard = new Chess(rightFen);
    } catch {
      return undefined;
    }
    if (stateNeedsCanonicalization) {
      const rightState = stateParts(rightBoard.fen());
      if (leftState.castling !== rightState.castling ||
          leftState.enPassant !== rightState.enPassant) return undefined;
    }
    const rightPieces = pieceSquares(rightBoard);
    if ([...new Set([...leftPieces.keys(), ...rightPieces.keys()])].some(
      (key) => (leftPieces.get(key)?.size ?? 0) !== (rightPieces.get(key)?.size ?? 0),
    )) return undefined;
    let relocations = 0;
    for (const [key, squares] of leftPieces) {
      const other = rightPieces.get(key)!;
      relocations += [...squares].filter((square) => !other.has(square)).length;
    }
    return relocations;
  };
}

export function chessPositionDistance(leftFen: string, rightFen: string): number | undefined {
  return prepareChessPositionDistance(leftFen)(rightFen);
}

export function indexRepertoirePositions(lines: AnalysisLine[]): IndexedPosition[] {
  type PositionPrefix = { fen: string; continuations: Map<string, PositionPrefix> };
  const indexed: IndexedPosition[] = [];
  const rootsByStartingFen = new Map<string, PositionPrefix>();
  for (const line of lines) {
    try {
      let board: Chess | undefined;
      let prefix: PositionPrefix;
      const existingRoot = rootsByStartingFen.get(line.startingFen);
      if (existingRoot) prefix = existingRoot;
      else {
        board = new Chess(line.startingFen);
        prefix = { fen: board.fen(), continuations: new Map() };
        rootsByStartingFen.set(line.startingFen, prefix);
      }
      indexed.push({ lineId: line.id, repertoireId: line.repertoireId, repertoireName: line.repertoireName, fen: prefix.fen, ply: 0, nextUci: line.moves[0] });
      for (const [moveIndex, uci] of line.moves.entries()) {
        let nextPrefix: PositionPrefix | undefined = prefix.continuations.get(uci);
        if (nextPrefix) board = undefined;
        else {
          board ??= new Chess(prefix.fen);
          board.move({ from: uci.slice(0, 2) as Square, to: uci.slice(2, 4) as Square, promotion: uci[4] || undefined });
          nextPrefix = { fen: board.fen(), continuations: new Map() };
          prefix.continuations.set(uci, nextPrefix);
        }
        prefix = nextPrefix;
        indexed.push({ lineId: line.id, repertoireId: line.repertoireId, repertoireName: line.repertoireName, fen: prefix.fen, ply: moveIndex + 1, nextUci: line.moves[moveIndex + 1] });
      }
    } catch {
      // Canonical validation owns diagnostics; unusable tails do not enter the index.
    }
  }
  return indexed;
}
