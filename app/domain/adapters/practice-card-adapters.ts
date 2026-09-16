import { Chess, Square } from "chess.js";
import {
  BackendQueueCard,
  PackagedPuzzle,
  PracticeCard,
  asCardId,
  asQueueEntryId,
  asRepertoireId,
} from "../../types";
import { movesToSanFormat } from "../../utils/chess";

// Maps queue transport records from the backend into domain practice cards.
export function mapQueueCardToPracticeCard(
  card: BackendQueueCard,
): PracticeCard {
  return {
    id: asCardId(`queue-${card.queue_entry_id}`),
    backendId: card.id,
    queueEntryId: asQueueEntryId(Number(card.queue_entry_id)),
    queueCycle: card.cycle,
    queueAttemptState: card.attempt_state,
    attemptFailed: Boolean(card.attempt_failed),
    suggestShorterPrefix:
      (card.recent_attempts_json?.match(/"again"/g)?.length ?? 0) >= 3,
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
    repertoireId: card.repertoire_id
      ? asRepertoireId(String(card.repertoire_id))
      : undefined,
  };
}

// Converts packaged puzzle records into domain practice cards.
export function mapPackagedPuzzleToPracticeCard(
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
      id: asCardId(`lichess-${record.PuzzleId}`),
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
