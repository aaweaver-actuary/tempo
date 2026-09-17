import {
  queueEnvelopeSchema,
  queueCardSchema,
  packagedPuzzleSchema,
} from "../schemas";
import {
  parseData,
  validRecords,
  reportDataDiagnostic,
} from "../../lib/validated-data";
import { Chess, Square } from "chess.js";
import {
  BackendQueueCard,
  PackagedPuzzle,
  PracticeCard,
  asCardId,
  asFenString,
  asQueueEntryId,
  asRepertoireId,
  asSanMove,
} from "../../types";
import { movesToSanFormat } from "../../utils/chess";
import { canonicalizeMoves } from "../../utils/canonical-line";

// Maps queue transport records from the backend into domain practice cards.
export function mapQueueCardToPracticeCard(
  raw: BackendQueueCard | unknown,
): PracticeCard {
  const card = parseData(queueCardSchema, raw, "queue card");
  const startingFen = asFenString(card.start_fen);
  const validatedLine = canonicalizeMoves(startingFen, card.moves);
  if (validatedLine.diagnostics.length) {
    throw new Error(
      `Invalid queue card ${card.id}: ${validatedLine.diagnostics[0].message}`,
    );
  }
  return {
    id: asCardId(`queue-${card.queue_entry_id}`),
    backendId: asCardId(card.id),
    queueEntryId: asQueueEntryId(Number(card.queue_entry_id)),
    queueCycle: card.cycle,
    queueAttemptState: card.attempt_state,
    attemptFailed: Boolean(card.attempt_failed),
    firstCleanPassAt: card.first_correct_at ?? undefined,
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
    startingFen,
    moves:
      card.content_type === "endgame"
        ? []
        : movesToSanFormat(startingFen, validatedLine.moves).map(asSanMove),
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
  try {
    record = parseData(packagedPuzzleSchema, record, "packaged puzzle");
    const board = new Chess(asFenString(record.FEN));
    const uciMoves = record.Moves.split(/\s+/).filter(Boolean);
    const setup = uciMoves.shift()!;
    board.move({
      from: setup.slice(0, 2) as Square,
      to: setup.slice(2, 4) as Square,
      promotion: setup[4],
    });
    const startingFen = board.fen();
    const moves = uciMoves.map((uci) =>
      asSanMove(
        board.move({
          from: uci.slice(0, 2) as Square,
          to: uci.slice(2, 4) as Square,
          promotion: uci[4],
        }).san,
      ),
    );
    return {
      id: asCardId(`lichess-${record.PuzzleId}`),
      kind: "puzzle",
      title: `Puzzle ${record.DeckPosition}`,
      subtitle: `Lichess · ${record.Rating}`,
      startingFen: asFenString(startingFen),
      moves,
      userMoveTarget: Math.ceil(moves.length / 2),
      sourceUrl: `https://lichess.org/training/${record.PuzzleId}`,
      orientation: new Chess(startingFen).turn() === "b" ? "black" : "white",
    };
  } catch {
    return null;
  }
}

export function queueCardsFromPayload(raw: unknown): PracticeCard[] {
  const body = parseData(queueEnvelopeSchema, raw, "daily queue");
  for (const diagnostic of body.diagnostics ?? [])
    reportDataDiagnostic(
      "daily queue",
      diagnostic,
      diagnostic.message,
      diagnostic.card_id,
    );
  return validRecords(queueCardSchema, body.cards, "queue card").flatMap(
    (record) => {
      try {
        return [mapQueueCardToPracticeCard(record)];
      } catch (error) {
        reportDataDiagnostic("queue card", record, String(error), record.id);
        return [];
      }
    },
  );
}
