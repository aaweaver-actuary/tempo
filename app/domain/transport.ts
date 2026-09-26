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
  Defense = "defense",
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
  latest_review_id?: number;
  parent_local_entry_id?: number;
  scheduling_mode?: "normal" | "light" | "hard";
  cycle?: number;
  attempt_state?: QueueAttemptStateValue;
  attempt_failed?: boolean;
  gameplay_priority_reason?: string | null;
  has_study_review?: boolean | 0 | 1;
  study_review_count?: number;
  admission_kind?: string | null;
  admission_source?: string | null;
  encounter_badges?: Array<"Seen recently" | "Frequent">;
  encounter_count_30d?: number;
  last_encountered_at?: string | null;
  first_correct_at?: string | null;
  recent_attempts_json?: string;
  start_fen: string;
  moves: string[];
  content_type: GamePhase | QueueContentType;
  repertoire_name: string;
  repertoire_source: RepertoireSource;
  source_ref?: string | null;
  is_main?: boolean;
  trained_color?: PieceColor | null;
  revision?: number;
  repertoire_id?: string;
  pending_validation?: boolean;
};
