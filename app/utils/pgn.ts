import { Chess, Square } from "chess.js";
import { STANDARD_FEN } from "../const";
import {
  GameViewRecord,
  asFenString,
  asGameId,
  asRepertoireId,
  asSanMove,
} from "../types";

function normalizeSpeed(value: unknown): GameViewRecord["speed"] {
  const normalized = String(value ?? "").toLowerCase();
  return normalized === "bullet" ||
    normalized === "blitz" ||
    normalized === "rapid" ||
    normalized === "classical"
    ? normalized
    : "unknown";
}

function normalizeResult(value: unknown): GameViewRecord["result"] {
  const normalized = String(value ?? "").toLowerCase();
  if (["1-0", "win", "won"].includes(normalized)) return "won";
  if (["0-1", "loss", "lost"].includes(normalized)) return "lost";
  if (["1/2-1/2", "draw"].includes(normalized)) return "draw";
  return "unknown";
}

function normalizeCoverageStatus(value: unknown): GameViewRecord["status"] {
  const normalized = String(value ?? "").toLowerCase();
  if (normalized === "covered") return "covered";
  if (["opponent gap", "opponent repertoire gap"].includes(normalized))
    return "opponent gap";
  if (normalized === "player deviation") return "player deviation";
  if (normalized === "out of book") return "out of book";
  if (["no repertoire", "no applicable repertoire"].includes(normalized))
    return "no repertoire";
  return "unknown";
}

function normalizeAnalysisState(
  value: unknown,
): GameViewRecord["analysisState"] {
  const normalized = String(value ?? "").toLowerCase();
  if (normalized === "pending") return "pending";
  if (normalized === "complete") return "complete";
  if (normalized === "failed") return "failed";
  return "unknown";
}

// Converts an imported game record from a generic object to a structured GameViewRecord.
export function importGameAndReformatToGameViewRecord(
  value: Record<string, unknown>,
): GameViewRecord | null {
  const startFen = asFenString(String(value.start_fen || STANDARD_FEN));
  const uciMoves = Array.isArray(value.moves) ? value.moves.map(String) : [];
  const color = value.color === "black" ? "black" : "white";
  const board = new Chess(startFen);
  const moves = [] as GameViewRecord["moves"];
  try {
    for (const uci of uciMoves)
      moves.push(
        asSanMove(
          board.move({
            from: uci.slice(0, 2) as Square,
            to: uci.slice(2, 4) as Square,
            promotion: uci[4],
          }).san,
        ),
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
  const status = normalizeCoverageStatus(value.classification);
  const flag =
    major !== null
      ? `First major mistake · move ${Math.floor(major / 2) + 1}`
      : missed !== null
        ? `Missed punishment · move ${Math.floor(missed / 2) + 1}`
        : divergence !== null
          ? `First repertoire divergence · move ${Math.floor(divergence / 2) + 1}`
          : "No flagged position";
  return {
    id: asGameId(String(value.id)),
    source: String(value.provider) === "chess.com" ? "Chess.com" : "Lichess",
    date: String(value.played_at || "").slice(0, 10),
    speed: normalizeSpeed(value.speed),
    color,
    result: normalizeResult(value.result),
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
    analysisState: normalizeAnalysisState(value.analysis_state),
    repertoireId: value.repertoire_id
      ? asRepertoireId(String(value.repertoire_id))
      : undefined,
    matchedPlayerDecisions: Number(value.matched_player_decisions ?? 0),
    repertoireOpportunities: Number(value.repertoire_opportunities ?? 0),
    deepestCoveredPly: Number(value.deepest_covered_ply ?? 0),
    firstOpponentGapPly: typeof value.first_opponent_gap_ply === "number" ? value.first_opponent_gap_ply : undefined,
    outOfBookPly: typeof value.out_of_book_ply === "number" ? value.out_of_book_ply : undefined,
    adherence: typeof value.adherence === "number" ? value.adherence : undefined,
    timeline: Array.isArray(value.timeline)
      ? value.timeline.flatMap((event) => {
          if (!event || typeof event !== "object") return [];
          const candidate = event as Record<string, unknown>;
          return typeof candidate.ply === "number" && typeof candidate.kind === "string"
            ? [{ ply: candidate.ply, kind: candidate.kind }]
            : [];
        })
      : [],
    deviationCardId: value.deviation_card_id ? String(value.deviation_card_id) : undefined,
  };
}
