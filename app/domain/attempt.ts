import { Chess } from "chess.js";
import { canReplayPositionStudyOffline } from "./position-offline-plan";
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
  attemptId?: string;
  /** Generated locally for this ordinary attempt, never restored from an issued plan. */
  freshEvidenceAttempt?: boolean;
};

export function attemptEntryKey(card: PracticeCard): string {
  const positionIdentity = card.positionStudyMode === "position"
    ? `:position:${card.positionPresentation?.plan_id ?? "held"}:${card.positionPresentation?.attempt_id ?? ""}` : "";
  return `${card.backendId ?? card.id}:${card.queueEntryId ?? card.id}:${card.queueCycle ?? 0}:${card.revision ?? 1}:${card.startingFen}:${card.moves.join(" ")}${positionIdentity}`;
}

/** Position attempts use the server's frozen identity through reload/restart. */
export function trainingAttemptId(card: PracticeCard | undefined): string {
  return card?.positionPresentation?.attempt_id ?? crypto.randomUUID();
}

export function positionStudyUnavailable(card: PracticeCard, offline = false): string | undefined {
  if (card.positionStudyMode !== "position") return undefined;
  if (card.positionStudyHold) return `Position study unavailable: ${card.positionStudyHold}`;
  if (!card.positionPresentation) return "Position study unavailable: refresh the issued plan while connected.";
  if (offline && !canReplayPositionStudyOffline({
    positionStudyMode: card.positionStudyMode,
    positionPresentation: card.positionPresentation,
    originalCardId: card.backendId,
    originalQueueEntryId: card.queueEntryId,
    originalCardRevision: card.revision,
    expectedAttemptId: card.positionPresentation.attempt_id,
  })) return "Position study requires a connection. Reconnect to continue this issued plan.";
  return undefined;
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
