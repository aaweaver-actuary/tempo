import type { PracticeCard } from "../domain/cards";
import { useTrainingStore } from "../state/training-store";
import { localDayKey } from "../utils/local";
import { conflictedReviews, pendingReviews } from "./review-outbox";
import { pendingTrainingFailureContexts, isLegacyTrainingFailure } from "./training-failure-outbox";

export type PreparedNextIdentity = {
  queue_entry_id: number; card_id: string; revision: number; cycle: number; local_date: string;
};
export type PreparedContinuation = PreparedNextIdentity & { attempt_failed?: boolean };

function matchesIdentity(card: PracticeCard, identity: PreparedNextIdentity): boolean {
  return card.queueEntryId === identity.queue_entry_id && String(card.backendId ?? card.id) === identity.card_id &&
    card.revision === identity.revision && (card.queueCycle ?? 0) === identity.cycle;
}

export function capturePreparedNext(): PreparedNextIdentity | undefined {
  const state = useTrainingStore.getState();
  const next = state.practiceCards[state.activeCardIndex + 1];
  if (!state.isDatabaseQueueActive || state.isOfflineQueueActive || state.queueReadiness !== "ready" ||
      !next?.queueEntryId || !next.backendId || !next.revision || ["study", "defense"].includes(next.kind)) return;
  const localDate = state.queueLocalDate;
  if (!localDate || localDate !== localDayKey()) return;
  const identity = { queue_entry_id: Number(next.queueEntryId), card_id: String(next.backendId),
    revision: next.revision, cycle: next.queueCycle ?? 0, local_date: localDate };
  return nextIsLocallyEligible(identity) ? identity : undefined;
}

function nextIsLocallyEligible(identity: PreparedNextIdentity): boolean {
  return !pendingReviews().some(review => review.queueEntryId === identity.queue_entry_id && review.backendId === identity.card_id) &&
    !conflictedReviews().some(review => review.backendId === identity.card_id) &&
    !pendingTrainingFailureContexts().some(failure => failure.queueEntryId === identity.queue_entry_id && isLegacyTrainingFailure(failure));
}

export function advancePreparedContinuation(continuation: PreparedContinuation | undefined, attemptId: string, generation: number): boolean {
  if (!continuation) return false;
  const state = useTrainingStore.getState();
  const next = state.practiceCards[state.activeCardIndex + 1];
  if (!next || state.isOfflineQueueActive || state.serviceError || state.attempt.attemptId !== attemptId ||
      state.attempt.generation !== generation || state.queueLocalDate !== continuation.local_date ||
      continuation.local_date !== localDayKey() || !matchesIdentity(next, continuation) || !nextIsLocallyEligible(continuation)) return false;
  if (continuation.attempt_failed !== undefined && next.attemptFailed !== continuation.attempt_failed) {
    const cards = [...state.practiceCards];
    cards[state.activeCardIndex + 1] = { ...next, attemptFailed: continuation.attempt_failed };
    useTrainingStore.setState({ practiceCards: cards });
  }
  return useTrainingStore.getState().advanceCachedQueue();
}
