import { Chess } from "chess.js";
import type { EngineMove } from "../../lib/analysis-engines";
import { canonicalizeMoves } from "../../utils/canonical-line";
import { asFenString, asNonNegativeInteger, asSanMove, asUciMove } from "../shared";
import type { CandidateMove, ExplorerMove } from "../analysis";

export type RawExplorerMove = {
  uci: string;
  san?: string;
  white: number;
  draws: number;
  black: number;
};

function legalMove(startingFen: string, rawUci: string) {
  const board = new Chess(asFenString(startingFen));
  const uci = asUciMove(rawUci);
  const played = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
  return { uci, san: asSanMove(played.san) };
}

export function adaptEngineMoves(startingFen: string, records: readonly EngineMove[]): CandidateMove[] {
  asFenString(startingFen);
  return records.flatMap((record) => {
    try {
      const move = legalMove(startingFen, record.uci);
      return [{
        ...record,
        ...move,
        pv: record.pv ? canonicalizeMoves(startingFen, record.pv).moves : undefined,
      }];
    } catch {
      // Engines may emit null bestmoves for terminal positions; never paint them.
      return [];
    }
  });
}

export function adaptExplorerMoves(startingFen: string, records: readonly RawExplorerMove[]): ExplorerMove[] {
  return records.map((record) => ({
    ...legalMove(startingFen, record.uci),
    white: asNonNegativeInteger(record.white),
    draws: asNonNegativeInteger(record.draws),
    black: asNonNegativeInteger(record.black),
  }));
}
