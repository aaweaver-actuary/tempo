import type {
  CardId,
  DeckId,
  FenString,
  GamePhase,
  PieceColor,
  PuzzleId,
  QueueEntryId,
  RepertoireId,
  UciMove,
} from "./shared";
import type { QueueAttemptStateValue } from "./cards";
import type { RepertoireSource } from "./repertoire";

export type RawUciMoveSequence = string;

export enum QueueContentTypeEnum {
  Opening = "opening",
  Middlegame = "middlegame",
  Endgame = "endgame",
  Tactic = "tactic",
}

export type QueueContentType = `${QueueContentTypeEnum}`;

export type PackagedPuzzle = {
  DeckId: DeckId;
  DeckPosition: number;
  PuzzleId: PuzzleId;
  FEN: FenString;
  Moves: RawUciMoveSequence;
  Rating: number;
};

export type BackendQueueCard = {
  id: CardId;
  queue_entry_id: QueueEntryId;
  cycle?: number;
  attempt_state?: QueueAttemptStateValue;
  attempt_failed?: boolean;
  recent_attempts_json?: string;
  start_fen: FenString;
  moves: Array<UciMove>;
  content_type: GamePhase | QueueContentType;
  repertoire_name: string;
  repertoire_source: RepertoireSource;
  source_ref?: string;
  is_main?: boolean;
  trained_color?: PieceColor;
  revision?: number;
  repertoire_id?: RepertoireId;
};
