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
  randomValue = Math.random(),
): number[] {
  const activePosition = queue.indexOf(activeCardIndex);
  if (activePosition < 0 || queue.length < 2) return queue;
  const reorderedQueue = [...queue];
  reorderedQueue.splice(activePosition, 1);
  const insertionPosition = 1 + Math.floor(randomValue * reorderedQueue.length);
  reorderedQueue.splice(insertionPosition, 0, activeCardIndex);
  return reorderedQueue;
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
  teachingReadyCard: TeachingCardKey | "";
};
