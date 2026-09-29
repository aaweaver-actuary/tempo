import { Chess, type Square } from "chess.js";
import type * as z from "zod";
import { comparisonCardSchema } from "../domain/schemas";
export { comparisonCardSchema, comparisonCardsSchema } from "../domain/schemas";
export type ComparisonCard = z.infer<typeof comparisonCardSchema>;

export type ComparisonPosition = {
  cardId: string;
  fen: string;
  ply: number;
  nextUci?: string;
  distance: number;
  routeSan?: string[];
};

export type ComparisonRouteMove = { uci: string; san: string; fen: string };
export type ComparisonBoard = {
  id: string;
  label: string;
  cardId?: string;
  orientation?: "white" | "black";
  startingFen: string;
  history: ComparisonRouteMove[];
  cursor: number;
};

export type ComparisonLaunch = {
  sourceKey: string;
  source: ComparisonBoard;
  repertoireId?: string;
  returnView: "train" | "builder";
};

export function replayComparisonRoute(startingFen: string, moves: string[]): ComparisonRouteMove[] {
  const board = new Chess(startingFen);
  return moves.map((uci) => {
    const move = board.move({
      from: uci.slice(0, 2) as Square,
      to: uci.slice(2, 4) as Square,
      promotion: uci[4] || undefined,
    });
    if (!move) throw new Error(`Illegal saved move ${uci}`);
    return { uci, san: move.san, fen: board.fen() };
  });
}

export function comparisonFen(board: ComparisonBoard): string {
  return board.cursor === 0 ? board.startingFen : board.history[board.cursor - 1].fen;
}

export function comparisonPieceDifferences(leftFen: string, rightFen: string): string[] {
  const left = new Chess(leftFen);
  const right = new Chess(rightFen);
  const different: string[] = [];
  for (const row of left.board()) {
    for (const piece of row) {
      const square = piece?.square;
      if (square && (left.get(square)?.type !== right.get(square)?.type ||
          left.get(square)?.color !== right.get(square)?.color))
        different.push(square);
    }
  }
  for (const row of right.board()) {
    for (const piece of row) {
      if (piece && !left.get(piece.square) && !different.includes(piece.square))
        different.push(piece.square);
    }
  }
  return different;
}
