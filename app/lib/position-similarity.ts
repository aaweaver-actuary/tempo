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

export function chessPositionDistance(leftFen: string, rightFen: string): number | undefined {
  const leftState = stateParts(leftFen);
  const rightState = stateParts(rightFen);
  if (
    leftState.turn !== rightState.turn ||
    leftState.castling !== rightState.castling ||
    leftState.enPassant !== rightState.enPassant
  ) return undefined;
  let left: Chess;
  let right: Chess;
  try {
    left = new Chess(leftFen);
    right = new Chess(rightFen);
  } catch {
    return undefined;
  }
  const leftPieces = pieceSquares(left);
  const rightPieces = pieceSquares(right);
  if ([...new Set([...leftPieces.keys(), ...rightPieces.keys()])].some(
    (key) => (leftPieces.get(key)?.size ?? 0) !== (rightPieces.get(key)?.size ?? 0),
  )) return undefined;
  let relocations = 0;
  for (const [key, squares] of leftPieces) {
    const other = rightPieces.get(key)!;
    relocations += [...squares].filter((square) => !other.has(square)).length;
  }
  return relocations;
}

export function indexRepertoirePositions(lines: AnalysisLine[]): IndexedPosition[] {
  const indexed: IndexedPosition[] = [];
  for (const line of lines) {
    try {
      const board = new Chess(line.startingFen);
      indexed.push({ lineId: line.id, repertoireId: line.repertoireId, repertoireName: line.repertoireName, fen: board.fen(), ply: 0, nextUci: line.moves[0] });
      line.moves.forEach((uci, index) => {
        board.move({ from: uci.slice(0, 2) as Square, to: uci.slice(2, 4) as Square, promotion: uci[4] || undefined });
        indexed.push({ lineId: line.id, repertoireId: line.repertoireId, repertoireName: line.repertoireName, fen: board.fen(), ply: index + 1, nextUci: line.moves[index + 1] });
      });
    } catch {
      // Canonical validation owns diagnostics; unusable tails do not enter the index.
    }
  }
  return indexed;
}

export type TranspositionResult = IndexedPosition & {
  distance: number;
  path: string[];
  probability: number;
};

export async function searchMaiaTranspositions({
  startFen,
  targets,
  horizon = 4,
  analyze,
  matchPositions,
  signal,
}: {
  startFen: string;
  targets: IndexedPosition[];
  horizon?: number;
  analyze: (fen: string) => Promise<Array<{ uci: string; probability?: number }>>;
  matchPositions?: (fen: string, targets: IndexedPosition[]) => Promise<Array<IndexedPosition & { distance: number }>>;
  signal?: AbortSignal;
}): Promise<TranspositionResult[]> {
  type BeamNode = { fen: string; path: string[]; probability: number };
  let beam: BeamNode[] = [{ fen: startFen, path: [], probability: 1 }];
  const found: TranspositionResult[] = [];
  let examined = 0;
  const boundedHorizon = Math.max(2, Math.min(8, horizon));
  for (let depth = 0; depth < boundedHorizon && beam.length && examined < 200; depth += 1) {
    const expanded: BeamNode[] = [];
    for (const node of beam) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      const moves = (await analyze(node.fen)).slice(0, 5);
      for (const candidate of moves) {
        if (examined >= 200) break;
        examined += 1;
        try {
          const board = new Chess(node.fen);
          board.move({ from: candidate.uci.slice(0, 2) as Square, to: candidate.uci.slice(2, 4) as Square, promotion: candidate.uci[4] || undefined });
          const child: BeamNode = {
            fen: board.fen(),
            path: [...node.path, candidate.uci],
            probability: node.probability * (candidate.probability ?? 0.001),
          };
          expanded.push(child);
          const matches = matchPositions ? await matchPositions(child.fen, targets) : targets.flatMap(target => {
            const distance = chessPositionDistance(child.fen, target.fen);
            return distance !== undefined && distance <= 2 ? [{ ...target, distance }] : [];
          });
          if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
          for (const target of matches) found.push({ ...target, path: child.path, probability: child.probability });
        } catch {
          // Ignore stale or malformed model moves.
        }
      }
    }
    beam = expanded.sort((a, b) => b.probability - a.probability).slice(0, 24);
  }
  return [...new Map(found.sort((a, b) => a.distance - b.distance || b.probability - a.probability).map((item) => [`${item.lineId}:${item.ply}:${item.path.join("-")}`, item])).values()].slice(0, 5);
}
