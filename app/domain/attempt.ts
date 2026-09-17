import { Chess } from "chess.js";
import type { PracticeCard } from "./cards";
import { asFenString, type FenString, type MoveSquares } from "./shared";

export type AttemptPhase =
  | "playerTurn"
  | "opponentReplyPending"
  | "guided"
  | "feedbackPause"
  | "complete";
export type AttemptToken = {
  entryKey: string;
  generation: number;
  phase: AttemptPhase;
};

export function attemptEntryKey(card: PracticeCard): string {
  return `${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${card.revision ?? 1}:${card.startingFen}:${card.moves.join(" ")}`;
}

export function isAttemptPlayable(attempt: AttemptToken): boolean {
  return attempt.phase === "playerTurn" || attempt.phase === "guided";
}

export function isCurrentAttempt(
  current: AttemptToken,
  expected: AttemptToken,
): boolean {
  return (
    current.entryKey === expected.entryKey &&
    current.generation === expected.generation
  );
}

export function initialTrainingState(card: PracticeCard): {
  fen: FenString;
  step: number;
  lastMove: MoveSquares | undefined;
} {
  const position = new Chess(card.startingFen);
  const trainedTurn = card.orientation === "black" ? "b" : "w";
  if (
    card.kind === "opening" &&
    position.turn() !== trainedTurn &&
    card.moves[0]
  ) {
    const move = position.move(card.moves[0]);
    return {
      fen: asFenString(position.fen()),
      step: 1,
      lastMove: [move.from, move.to],
    };
  }
  return { fen: card.startingFen, step: 0, lastMove: undefined };
}
