import { Dispatch, SetStateAction, RefObject } from "react";
import { API_URL } from "../const";
import { initialTrainingState } from "../page";
import {
  PracticeCard,
  Feedback,
  PositionAnnotation,
  BackendQueueCard,
} from "../types";
import { practiceCardFromQueue } from "../utils/cards";
import { usesLocalApi } from "../utils/local";

export function fetchAndInitializeQueue(
  setDatabaseQueue: Dispatch<SetStateAction<boolean>>,
  setServiceError: Dispatch<SetStateAction<string>>,
  setPracticeCards: Dispatch<SetStateAction<PracticeCard[]>>,
  setDailyQueue: Dispatch<SetStateAction<number[]>>,
  setCardsLeft: Dispatch<SetStateAction<number>>,
  reviewPending: RefObject<boolean>,
  activeQueueEntry: RefObject<number | undefined>,
  setActiveCardIndex: Dispatch<SetStateAction<number>>,
  attemptGeneration: RefObject<number>,
  setFen: Dispatch<SetStateAction<string>>,
  setStep: Dispatch<SetStateAction<number>>,
  setFeedback: Dispatch<SetStateAction<Feedback>>,
  setLastMove: Dispatch<SetStateAction<[string, string] | undefined>>,
  setOpponentLastMove: Dispatch<SetStateAction<[string, string] | undefined>>,
  setLocked: Dispatch<SetStateAction<boolean>>,
  setShowHint: Dispatch<SetStateAction<boolean>>,
  setTeachingEncounterKey: Dispatch<SetStateAction<string | null>>,
  setAttemptFailed: Dispatch<SetStateAction<boolean>>,
  setFailureAnnotation: Dispatch<
    SetStateAction<PositionAnnotation | undefined>
  >,
  setFailureFen: Dispatch<SetStateAction<string>>,
): () => Promise<void> {
  return async () => {
    if (!usesLocalApi()) return;
    try {
      const response = await fetch(`${API_URL}/api/queue/today`);
      if (!response.ok) throw new Error();
      const body = (await response.json()) as { cards: BackendQueueCard[] };
      const playable = body.cards.map(practiceCardFromQueue);
      setDatabaseQueue(true);
      setServiceError("");
      setPracticeCards(playable);
      const queue = playable.map((_, index) => index);
      setDailyQueue(queue);
      setCardsLeft(queue.length);
      const retainedIndex = reviewPending.current
        ? -1
        : playable.findIndex(
            (item) => item.queueEntryId === activeQueueEntry.current,
          );
      setActiveCardIndex(Math.max(0, retainedIndex));
      if (retainedIndex >= 0) return;
      attemptGeneration.current += 1;
      activeQueueEntry.current = playable[0]?.queueEntryId;
      const first = playable[0];
      if (first) {
        const start = initialTrainingState(first);
        setFen(start.fen);
        setStep(start.step);
        setFeedback(first.attemptFailed ? "wrong" : "ready");
        setLastMove(start.lastMove);
        setOpponentLastMove(start.lastMove);
        setLocked(false);
        setShowHint(Boolean(first.attemptFailed));
        setTeachingEncounterKey(null);
        setAttemptFailed(Boolean(first.attemptFailed));
        setFailureAnnotation(undefined);
        setFailureFen(first.attemptFailed ? start.fen : "");
      }
    } catch {
      setServiceError(
        "Tempo could not reach its local service. Check that the launcher is still running, then retry.",
      );
      setCardsLeft(0);
      setDailyQueue([]);
    }
  };
}
