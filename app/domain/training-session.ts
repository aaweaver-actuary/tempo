import type { PositionAnnotation } from "./annotations";
import type { PracticeCard } from "./cards";
import type { LocalRepertoire } from "./repertoire";
import type { FenString, Feedback, MoveSquares } from "./shared";

export enum QueueModeEnum {
  Browser = "browser",
  Local = "local",
}

export type QueueMode = `${QueueModeEnum}`;

export type TrainingQueueState = {
  mode: QueueMode;
  practiceCards: PracticeCard[];
  dailyQueue: number[];
  activeCardIndex: number;
  cardsLeft: number;
  reviewed: number;
  queueNotice: string;
  importedRepertoires: LocalRepertoire[];
  firstCleanPasses: Set<string>;
};

export type AttemptLifecycleState = {
  fen: FenString;
  step: number;
  feedback: Feedback;
  lastMove: MoveSquares | undefined;
  opponentLastMove: MoveSquares | undefined;
  isLocked: boolean;
  boardAttempt: number;
  showHint: boolean;
  attemptFailed: boolean;
  failureFen: FenString;
  failureAnnotation: PositionAnnotation | undefined;
  teachingEncounterKey: string | null;
  teachingReadyCard: string;
};
