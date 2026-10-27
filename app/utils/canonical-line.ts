import { canonicalizeMoves as canonicalizePackageMoves } from "@tempo/chess-line/interfaces";
import { asUciMove, type AnalysisLine, type CanonicalLine } from "../types";
export { canonicalFenKey, sanForUci } from "@tempo/chess-line/interfaces";

export function canonicalizeMoves(startingFen: string, inputMoves: readonly string[]) {
  const result = canonicalizePackageMoves(startingFen, inputMoves);
  return { ...result, moves: result.moves.map(asUciMove) };
}

export function canonicalizeLine(line: AnalysisLine): CanonicalLine {
  const result = canonicalizeMoves(
    line.startingFen,
    line.moves.map(String),
  );
  return {
    ...line,
    moves: result.moves,
    validation: {
      isValid: result.diagnostics.length === 0,
      isTruncated: result.moves.length < line.moves.length,
      diagnostics: result.diagnostics,
    },
  };
}
