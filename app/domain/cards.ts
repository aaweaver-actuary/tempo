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
  Defense = "defense",
  Study = "study",
}

export enum QueueAttemptState {
  Clean = "clean",
  Guided = "guided",
  Reinforcement = "reinforcement",
  Again = "again",
  Gameplay = "gameplay",
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
  priorityReason?: string;
  encounterBadges?: Array<"Seen recently" | "Frequent">;
  encounterCount30d?: number;
  lastEncounteredAt?: string;
  firstCleanPassAt?: string;
  hasPriorStudyReview?: boolean;
  priorStudyReviewCount?: number;
  suggestShorterPrefix?: boolean;
  prefixSplitLatestFailureId?: number;
  orientation?: PieceColor;
  revision?: number;
  repertoireId?: RepertoireId;
  openingDecisionManifest?: import("./opening-evidence").OpeningDecisionManifest;
  openingEvidenceDiagnostic?: string;
  openingEvidenceStudyTimezone?: string;
  openingEvidenceOriginEntryId?: number;
  openingEvidenceParentAttemptId?: string;
  editingIntent?: "standard" | "shorten-prefix";
  defenseCandidateId?: string;
  studyId?: string;
  studyExerciseId?: string;
  studySnapshot?: import("./study-exercises").StudySnapshot;
};
