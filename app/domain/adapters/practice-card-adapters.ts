import {
  queueEnvelopeSchema,
  queueCardSchema,
  packagedPuzzleSchema,
} from "../schemas";
import { openingDecisionManifestSchema } from "../opening-evidence";
import { studySnapshotSchema } from "../study-exercises";
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

export function queueRecordWithCompatibleOpeningEvidence(raw: unknown): unknown {
  if (!raw || typeof raw !== "object" || !("opening_decision_manifest" in raw) || raw.opening_decision_manifest === undefined) return raw;
  const parsed = openingDecisionManifestSchema.safeParse(raw.opening_decision_manifest);
  if (parsed.success) return raw;
  const record = { ...raw } as Record<string, unknown>;
  delete record.opening_decision_manifest;
  record.opening_evidence_diagnostic = "Opening evidence omitted: invalid manifest. Refresh the queue or inspect service diagnostics.";
  reportDataDiagnostic("opening evidence", raw, String(record.opening_evidence_diagnostic));
  return record;
}

// Maps queue transport records from the backend into domain practice cards.
export function mapQueueCardToPracticeCard(
  raw: BackendQueueCard | unknown,
): PracticeCard {
  const card = parseData(queueCardSchema, queueRecordWithCompatibleOpeningEvidence(raw), "queue card");
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
    priorityReason: card.gameplay_priority_reason ?? undefined,
    encounterBadges: card.encounter_badges,
    encounterCount30d: card.encounter_count_30d,
    lastEncounteredAt: card.last_encountered_at ?? undefined,
    firstCleanPassAt: card.first_correct_at ?? undefined,
    hasPriorStudyReview: card.has_study_review ?? Boolean(card.first_correct_at),
    priorStudyReviewCount: card.study_review_count,
    suggestShorterPrefix:
      card.kind === "prefix" &&
      card.prefix_split_offer_available !== false &&
      (card.recent_attempts_json?.match(/"again"/g)?.length ?? 0) >= 3,
    prefixSplitLatestFailureId: card.latest_failed_review_id,
    kind:
      card.content_type === "study_exercise"
        ? "study"
        : card.content_type === "defense"
        ? "defense"
        : card.content_type === "tactic"
        ? "puzzle"
        : card.content_type === "endgame"
          ? "endgame"
          : "opening",
    title:
      card.content_type === "study_exercise"
        ? card.repertoire_name
        : card.content_type === "defense"
        ? "Defensive decision"
        : card.content_type === "tactic"
        ? "Tactics review"
        : card.content_type === "endgame"
          ? "Endgame study"
          : card.repertoire_name,
    subtitle:
      card.content_type === "study_exercise"
        ? "Study exercise"
        : card.content_type === "defense"
        ? "From an analyzed game"
        : card.content_type === "tactic"
        ? card.repertoire_id === "__tactics__" ? `Lichess puzzle ${card.source_ref ?? ""}`
          : card.repertoire_id === "__game_tactics__" ? "From an analyzed game" : "Captured tactic"
        : card.repertoire_source,
    startingFen,
    moves:
      card.content_type === "endgame"
        ? []
        : movesToSanFormat(startingFen, validatedLine.moves).map(asSanMove),
    userMoveTarget: card.content_type === "defense" ? 1 : Math.ceil(card.moves.length / 2),
    sourceUrl: card.content_type === "tactic" && card.repertoire_id === "__tactics__" && card.source_ref
      ? `https://lichess.org/training/${card.source_ref}`
      : undefined,
    orientation:
      card.content_type === "defense"
        ? (card.trained_color ?? "white")
        : card.content_type === "tactic"
        ? new Chess(card.start_fen).turn() === "b"
          ? "black"
          : "white"
        : (card.trained_color ?? "white"),
    revision: card.revision ?? 1,
    openingDecisionManifest: card.content_type === "opening" ? card.opening_decision_manifest : undefined,
    openingEvidenceDiagnostic: card.opening_evidence_diagnostic,
    openingEvidenceStudyTimezone: card.opening_evidence_study_timezone,
    openingEvidenceOriginEntryId: card.opening_evidence_origin_queue_entry_id ?? Number(card.queue_entry_id),
    openingEvidenceParentAttemptId: card.opening_evidence_parent_attempt_id,
    defenseCandidateId: card.content_type === "defense" ? card.source_ref ?? undefined : undefined,
    studyId: card.study_id ?? undefined,
    studyExerciseId: card.study_exercise_id ?? undefined,
    studySnapshot: card.study_snapshot ? studySnapshotSchema.safeParse(card.study_snapshot).data : undefined,
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
  return validRecords(queueCardSchema, body.cards.map(queueRecordWithCompatibleOpeningEvidence), "queue card").flatMap(
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
