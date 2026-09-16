import type {
  GamePhase,
  PieceColor,
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
  DeckId: string;
  DeckPosition: number;
  PuzzleId: string;
  FEN: string;
  Moves: RawUciMoveSequence;
  Rating: number;
};

export type BackendQueueCard = {
  id: string;
  queue_entry_id: number;
  cycle?: number;
  attempt_state?: QueueAttemptStateValue;
  attempt_failed?: boolean;
  first_correct_at?: string | null;
  recent_attempts_json?: string;
  start_fen: string;
  moves: string[];
  content_type: GamePhase | QueueContentType;
  repertoire_name: string;
  repertoire_source: RepertoireSource;
  source_ref?: string;
  is_main?: boolean;
  trained_color?: PieceColor;
  revision?: number;
  repertoire_id?: string;
};
