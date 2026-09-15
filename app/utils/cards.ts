import { Chess, Square } from "chess.js";
import {
  AnalysisLine,
  BackendQueueCard,
  CardNameSource,
  PackagedPuzzle,
  PieceColor,
  PracticeCard,
} from "../types";
import { movesToSanFormat } from "./chess";

// Converts a backend queue card into a practice card for the training interface.
export function practiceCardFromQueue(card: BackendQueueCard): PracticeCard {
  return {
    id: `queue-${card.queue_entry_id}`,
    backendId: card.id,
    queueEntryId: card.queue_entry_id,
    kind:
      card.content_type === "tactic"
        ? "puzzle"
        : card.content_type === "endgame"
          ? "endgame"
          : "opening",
    title:
      card.content_type === "tactic"
        ? "Tactics review"
        : card.content_type === "endgame"
          ? "Endgame study"
          : card.repertoire_name,
    subtitle:
      card.content_type === "tactic"
        ? `Lichess puzzle ${card.source_ref ?? ""}`
        : card.repertoire_source,
    startingFen: card.start_fen,
    moves:
      card.content_type === "endgame"
        ? []
        : movesToSanFormat(card.start_fen, card.moves),
    userMoveTarget: Math.ceil(card.moves.length / 2),
    sourceUrl: card.source_ref
      ? `https://lichess.org/training/${card.source_ref}`
      : undefined,
    orientation:
      card.content_type === "tactic"
        ? new Chess(card.start_fen).turn() === "b"
          ? "black"
          : "white"
        : (card.trained_color ?? "white"),
    revision: card.revision ?? 1,
    repertoireId: card.repertoire_id,
  };
}

// Returns the color that the user is expected to play for a given practice card.
export function trainedColor(card: PracticeCard): PieceColor {
  if (card.orientation) return card.orientation;
  return card.kind === "opening"
    ? "white"
    : new Chess(card.startingFen).turn() === "b"
      ? "black"
      : "white";
}

// Returns a human-readable name for a given analysis line, either using its title or the SAN moves.
export function getHumanReadableLineName(
  line: Pick<AnalysisLine, CardNameSource>,
) {
  if (
    line.title &&
    !/^(analysis branch|line|variation)$/i.test(line.title.trim())
  )
    return line.title;
  try {
    return movesToSanFormat(line.startingFen, line.moves).join(" ");
  } catch {
    return line.moves.join(" ");
  }
}

export function convertPackagedPuzzleRecordIntoPracticeCard(
  record: PackagedPuzzle,
): PracticeCard | null {
  const board = new Chess(record.FEN);
  const uciMoves = record.Moves.split(/\s+/).filter(Boolean);
  try {
    const setup = uciMoves.shift()!;
    board.move({
      from: setup.slice(0, 2) as Square,
      to: setup.slice(2, 4) as Square,
      promotion: setup[4],
    });
    const startingFen = board.fen();
    const moves = uciMoves.map(
      (uci) =>
        board.move({
          from: uci.slice(0, 2) as Square,
          to: uci.slice(2, 4) as Square,
          promotion: uci[4],
        }).san,
    );
    return {
      id: `lichess-${record.PuzzleId}`,
      kind: "puzzle",
      title: `Puzzle ${record.DeckPosition}`,
      subtitle: `Lichess · ${record.Rating}`,
      startingFen,
      moves,
      userMoveTarget: Math.ceil(moves.length / 2),
      sourceUrl: `https://lichess.org/training/${record.PuzzleId}`,
      orientation: new Chess(startingFen).turn() === "b" ? "black" : "white",
    };
  } catch {
    return null;
  }
}
