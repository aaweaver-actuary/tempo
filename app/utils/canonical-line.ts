import { Chess, type Square } from "chess.js";
import type { CanonicalLine, AnalysisLine, LineDiagnostic } from "../types";

const UCI_MOVE = /^[a-h][1-8][a-h][1-8][qrbn]?$/i;
const NULL_MOVES = new Set(["0000", "--", "Z0"]);

export function canonicalFenKey(fen: string): string {
  return fen.trim().split(/\s+/).slice(0, 4).join(" ");
}

export function canonicalizeMoves(
  startingFen: string,
  inputMoves: readonly string[],
): { moves: string[]; diagnostics: LineDiagnostic[]; finalFen: string } {
  let board: Chess;
  try {
    board = new Chess(startingFen);
  } catch {
    board = new Chess();
    return {
      moves: [],
      diagnostics: [
        { ply: 0, move: "", kind: "invalid", message: "Invalid starting FEN" },
      ],
      finalFen: board.fen(),
    };
  }

  const moves: string[] = [];
  const diagnostics: LineDiagnostic[] = [];
  for (let index = 0; index < inputMoves.length; index += 1) {
    const value = String(inputMoves[index] ?? "").trim();
    if (NULL_MOVES.has(value)) {
      diagnostics.push({
        ply: index,
        move: value,
        kind: "null",
        message: `Stopped at null move ${value}`,
      });
      break;
    }
    try {
      const played = UCI_MOVE.test(value)
        ? board.move({
            from: value.slice(0, 2).toLowerCase() as Square,
            to: value.slice(2, 4).toLowerCase() as Square,
            promotion: value[4]?.toLowerCase() || undefined,
          })
        : board.move(value);
      moves.push(`${played.from}${played.to}${played.promotion ?? ""}`);
    } catch {
      diagnostics.push({
        ply: index,
        move: value,
        kind: UCI_MOVE.test(value) ? "illegal" : "invalid",
        message: `${UCI_MOVE.test(value) ? "Illegal" : "Invalid"} move ${value || "(empty)"}`,
      });
      break;
    }
  }
  return { moves, diagnostics, finalFen: board.fen() };
}

export function canonicalizeLine(line: AnalysisLine): CanonicalLine {
  const result = canonicalizeMoves(line.startingFen, line.moves);
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

export function sanForUci(fen: string, uci: string): string | undefined {
  try {
    const board = new Chess(fen);
    const move = board.move({
      from: uci.slice(0, 2) as Square,
      to: uci.slice(2, 4) as Square,
      promotion: uci[4] || undefined,
    });
    return move.san;
  } catch {
    return undefined;
  }
}
