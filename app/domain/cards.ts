import type {
  CardId,
  FenString,
  PieceColor,
  QueueEntryId,
  RepertoireId,
  SanMove,
} from "./shared";

export enum CardKindEnum {
  Opening = "opening",
  Middlegame = "middlegame",
  Endgame = "endgame",
  Puzzle = "puzzle",
}

export enum QueueAttemptState {
  Clean = "clean",
  Guided = "guided",
  Reinforcement = "reinforcement",
  Again = "again",
}

export type CardKind = `${CardKindEnum}`;
export type QueueAttemptStateValue = `${QueueAttemptState}`;
export type RequiredUserMoveCount = number;

export type PracticeCard = {
  id: CardId;
  kind: CardKind;
  title: string;
  subtitle: string;
  startingFen: FenString;
  moves: Array<SanMove>;
  userMoveTarget: RequiredUserMoveCount;
  nextMove?: SanMove;
  sourceUrl?: string;
  backendId?: CardId;
  queueEntryId?: QueueEntryId;
  queueCycle?: number;
  queueAttemptState?: QueueAttemptStateValue;
  attemptFailed?: boolean;
  firstCleanPassAt?: string;
  suggestShorterPrefix?: boolean;
  orientation?: PieceColor;
  revision?: number;
  repertoireId?: RepertoireId;
  editingIntent?: "standard" | "shorten-prefix";
};
