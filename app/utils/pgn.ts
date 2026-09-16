import { Chess, Square } from "chess.js";
import { STANDARD_FEN } from "../const";
import { GameViewRecord } from "../types";

// Converts an imported game record from a generic object to a structured GameViewRecord.
export function importGameAndReformatToGameViewRecord(
  value: Record<string, unknown>,
): GameViewRecord | null {
  const startFen = String(value.start_fen || STANDARD_FEN);
  const uciMoves = Array.isArray(value.moves) ? value.moves.map(String) : [];
  const color = value.color === "black" ? "black" : "white";
  const board = new Chess(startFen);
  const moves: string[] = [];
  try {
    for (const uci of uciMoves)
      moves.push(
        board.move({
          from: uci.slice(0, 2) as Square,
          to: uci.slice(2, 4) as Square,
          promotion: uci[4],
        }).san,
      );
  } catch {
    return null;
  }
  const major =
    typeof value.major_mistake_ply === "number"
      ? value.major_mistake_ply
      : null;
  const missed =
    typeof value.missed_punishment_ply === "number"
      ? value.missed_punishment_ply
      : null;
  const divergence =
    typeof value.divergence_ply === "number" ? value.divergence_ply : null;
  const flagPly = major ?? missed ?? divergence ?? 0;
  const status = String(value.classification || "no applicable repertoire");
  const flag =
    major !== null
      ? `First major mistake · move ${Math.floor(major / 2) + 1}`
      : missed !== null
        ? `Missed punishment · move ${Math.floor(missed / 2) + 1}`
        : divergence !== null
          ? `First repertoire divergence · move ${Math.floor(divergence / 2) + 1}`
          : "No flagged position";
  return {
    id: String(value.id),
    source: String(value.provider) === "chess.com" ? "Chess.com" : "Lichess",
    date: String(value.played_at || "").slice(0, 10),
    speed: String(value.speed || "unknown"),
    color,
    result: String(value.result || "*"),
    opening: String(value.opening_name || "Unclassified opening"),
    status,
    detail:
      divergence !== null
        ? `Diverged at move ${Math.floor(divergence / 2) + 1}`
        : status,
    flag,
    flagPly,
    moves,
    startFen,
    analysisState: String(value.analysis_state ?? "pending"),
    repertoireId: value.repertoire_id ? String(value.repertoire_id) : undefined,
  };
}
