import { Chess } from "chess.js";
import { STANDARD_FEN } from "../const";

export function lichessAnalysisUrl(
  moves: string[],
  startingFen = STANDARD_FEN,
) {
  if (moves.length === 0 && startingFen === STANDARD_FEN)
    return "https://lichess.org/analysis/standard";
  const chess = new Chess(startingFen);
  for (const move of moves) chess.move(move);
  return `https://lichess.org/analysis/standard/${encodeURIComponent(chess.fen())}`;
}
