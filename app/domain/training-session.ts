import type { PositionAnnotation } from "./annotations";
import type { PracticeCard } from "./cards";
import type { LocalRepertoire } from "./repertoire";
import type { AttemptToken } from "./attempt";
import type { FenString, Feedback, MoveSquares, TeachingCardKey } from "./shared";

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

export function buryQueuedCard(
  queue: number[],
  activeCardIndex: number,
): number[] {
  if (!queue.includes(activeCardIndex)) return queue;
  return queue.filter((cardIndex) => cardIndex !== activeCardIndex);
}

export type AttemptLifecycleState = {
  fen: FenString;
  step: number;
  feedback: Feedback;
  lastMove: MoveSquares | undefined;
  opponentLastMove: MoveSquares | undefined;
  attempt: AttemptToken;
  boardAttempt: number;
  showHint: boolean;
  attemptFailed: boolean;
  failureFen: FenString | undefined;
  failureAnnotation: PositionAnnotation | undefined;
  teachingEncounterKey: string | null;
  assistedThisAttempt: boolean;
  teachingReadyCard: TeachingCardKey | "";
};
