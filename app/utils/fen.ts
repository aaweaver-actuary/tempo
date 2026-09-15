import { Chess, Square } from "chess.js";
import { STANDARD_FEN } from "../const";

// Returns the FEN string after applying a specified number of moves from the given starting FEN.
export function fenAfterMoves(
  moves: string[],
  count: number,
  startingFen = STANDARD_FEN,
): string {
  const chess = new Chess(startingFen);
  for (const move of moves.slice(0, count)) chess.move(move);
  return chess.fen();
}

// Returns a new FEN string with the specified square edited to contain the given piece.
export function addPieceToFenString(
  fen: string,
  square: Square,
  piece: string,
): string {
  const [placement, ...state] = fen.trim().split(/\s+/);
  const rows = placement
    .split("/")
    .map((row) =>
      row
        .split("")
        .flatMap((value) =>
          /\d/.test(value) ? Array(Number(value)).fill("") : [value],
        ),
    );
  const rank = 8 - Number(square[1]);
  const file = square.charCodeAt(0) - 97;
  rows[rank][file] = piece;
  const compact = rows
    .map((row) => {
      let empty = 0;
      let result = "";
      for (const value of row) {
        if (!value) {
          empty += 1;
          continue;
        }
        if (empty) {
          result += empty;
          empty = 0;
        }
        result += value;
      }
      return result + (empty || "");
    })
    .join("/");
  return `${compact} ${state.join(" ")}`;
}

export function movePieceFromOneSquareToAnotherInFenString(
  fen: string,
  from: Square,
  to: Square,
): string {
  const chess = new Chess(fen);
  const piece = chess.get(from);
  if (!piece) return fen;
  const symbol = piece.color === "w" ? piece.type.toUpperCase() : piece.type;
  return addPieceToFenString(addPieceToFenString(fen, from, ""), to, symbol);
}

export function editFenSquare(
  fen: string,
  square: Square,
  piece: string,
): string {
  return addPieceToFenString(fen, square, piece);
}

export function moveFenPiece(
  fen: string,
  from: Square,
  to: Square,
): string {
  return movePieceFromOneSquareToAnotherInFenString(fen, from, to);
}
