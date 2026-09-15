import { Chess, Square } from "chess.js";
import { STANDARD_FEN } from "../const";

// Returns an array of SAN (Standard Algebraic Notation) moves for a given starting FEN
// and a list of moves in either SAN or UCI format.
export function movesToSanFormat(
  startingFen: string,
  moves: string[],
): string[] {
  const board = new Chess(startingFen);
  return moves.map((value) => {
    const move = /^[a-h][1-8][a-h][1-8][qrbn]?$/.test(value)
      ? board.move({
          from: value.slice(0, 2) as Square,
          to: value.slice(2, 4) as Square,
          promotion: value[4],
        })
      : board.move(value);
    return move.san;
  });
}

// Returns the UCI representation of a line of SAN moves, starting from the given FEN.
export function convertSanToUci(
  sanMoves: string[],
  startingFen = STANDARD_FEN,
): string[] {
  const chess = new Chess(startingFen);
  return sanMoves.map((san) => {
    const move = chess.move(san);
    return `${move.from}${move.to}${move.promotion ?? ""}`;
  });
}
