import { Chess } from "chess.js";
import { engineMoveSchema, explorerMoveSchema } from "../schemas";
import { parseData, reportDataDiagnostic } from "../../lib/validated-data";
import { canonicalizeMoves } from "../../utils/canonical-line";
import {
  asFenString,
  asNonNegativeInteger,
  asSanMove,
  asUciMove,
} from "../shared";
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
  const played = board.move({
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci[4],
  });
  return { uci, san: asSanMove(played.san) };
}

export function adaptEngineMoves(
  startingFen: string,
  records: readonly unknown[],
): CandidateMove[] {
  asFenString(startingFen);
  return records.flatMap((raw) => {
    try {
      const record = parseData(engineMoveSchema, raw, "engine move");
      const move = legalMove(startingFen, record.uci);
      return [
        {
          ...record,
          ...move,
          pv: record.pv
            ? canonicalizeMoves(startingFen, record.pv).moves
            : undefined,
        },
      ];
    } catch (error) {
      reportDataDiagnostic("engine move", raw, String(error));
      // Engines may emit null bestmoves for terminal positions; never paint them.
      return [];
    }
  });
}

export function adaptExplorerMoves(
  startingFen: string,
  records: readonly unknown[],
): ExplorerMove[] {
  return records.flatMap((raw) => {
    try {
      const record = parseData(explorerMoveSchema, raw, "explorer move");
      return [
        {
          ...legalMove(startingFen, record.uci),
          white: asNonNegativeInteger(record.white),
          draws: asNonNegativeInteger(record.draws),
          black: asNonNegativeInteger(record.black),
        },
      ];
    } catch (error) {
      reportDataDiagnostic("explorer move", raw, String(error));
      return [];
    }
  });
}
