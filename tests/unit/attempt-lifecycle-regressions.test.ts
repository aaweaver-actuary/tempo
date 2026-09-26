import { beforeEach, describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import {
  useTrainingStore,
  selectHomeViewState,
  selectTrainingViewState,
} from "../../app/state/training-store";
import { attemptEntryKey } from "../../app/domain/attempt";
import {
  asCardId,
  asFenString,
  asQueueEntryId,
  asSanMove,
  type PracticeCard,
} from "../../app/types";

const card: PracticeCard = {
  id: asCardId("reviewed-card"),
  backendId: asCardId("persisted-card"),
  queueEntryId: asQueueEntryId(42),
  kind: "opening",
  title: "Reviewed",
  subtitle: "",
  orientation: "white",
  revision: 1,
  startingFen: asFenString(
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  ),
  moves: [asSanMove("e4"), asSanMove("e5"), asSanMove("Nf3")],
  userMoveTarget: 2,
  firstCleanPassAt: "2026-09-15T12:00:00Z",
};

beforeEach(() => useTrainingStore.getState().initializeCardState(card));

describe("review attempt reliability", () => {
  it("advances to a prefetched card before review persistence and retains the total queue count", () => {
    const nextCard = { ...card, id: asCardId("next-card"), queueEntryId: asQueueEntryId(43) };
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card, nextCard], true, 135);
    expect(useTrainingStore.getState().advanceCachedQueue()).toBe(true);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(43);
    expect(useTrainingStore.getState().cardsLeft).toBe(134);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
  });
  it("pauses an active attempt when queue reconciliation removes its entry", () => {
    const replacement = { ...card, id: asCardId("replacement"), queueEntryId: asQueueEntryId(44) };
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    store.setStep(1);
    store.hydrateLocalQueue([replacement]);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(42);
    expect(useTrainingStore.getState().attempt.phase).toBe("feedbackPause");
    expect(useTrainingStore.getState().serviceError).toContain("no longer in today's queue");
    store.hydrateLocalQueue([replacement]);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(44);
  });
  it("late queue responses cannot replace a newer playable queue entry", async () => {
    const responses: ((response: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>((resolve) => responses.push(resolve))),
    );
    const first = fetchAndInitializeQueue();
    const second = fetchAndInitializeQueue();
    const payload = (id: number) =>
      Response.json({
        cards: [
          {
            id: "persisted-card",
            queue_entry_id: id,
            start_fen: card.startingFen,
            moves: ["e2e4"],
            content_type: "opening",
            repertoire_name: "Prep",
            repertoire_source: "PGN",
          },
        ],
      });
    responses[1](payload(44));
    await second;
    responses[0](payload(43));
    await first;
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(44);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
  });
  it("superseded queue request releases its browser connection without replacing the current attempt", async () => {
    let firstRequestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn()
      .mockImplementationOnce((_input: RequestInfo | URL, options: RequestInit) => {
        firstRequestSignal = options.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          options.signal?.addEventListener("abort", () => reject(options.signal?.reason));
        });
      })
      .mockResolvedValueOnce(Response.json({ cards: [{
        id: "persisted-card", queue_entry_id: 44, start_fen: card.startingFen,
        moves: ["e2e4"], content_type: "opening", repertoire_name: "Prep",
        repertoire_source: "PGN",
      }] }));
    vi.stubGlobal("fetch", fetchMock);

    const first = fetchAndInitializeQueue();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const second = fetchAndInitializeQueue();
    await Promise.all([first, second]);
    expect(firstRequestSignal?.aborted).toBe(true);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(44);
    expect(useTrainingStore.getState().serviceError).toBe("");
  });
  it("retryable queue contention retries before reporting a failure and retains the active attempt", async () => {
    const payload = {
      cards: [{
        id: "persisted-card",
        queue_entry_id: 44,
        start_fen: card.startingFen,
        moves: ["e2e4"],
        content_type: "opening",
        repertoire_name: "Prep",
        repertoire_source: "PGN",
      }],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(Response.json({ detail: "The local database is busy with background work. Retry this action.", retryable: true }, { status: 503 }))
        .mockResolvedValueOnce(Response.json(payload)),
    );
    await fetchAndInitializeQueue();
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(44);
    expect(useTrainingStore.getState().serviceError).toBe("");
  });
  it("timed-out queue read retains a completed tactic for an actionable retry", async () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true, 1);
    store.setStep(card.moves.length);
    store.setFeedback("complete");
    store.setAttemptPhase("feedbackPause");
    const completedAttempt = useTrainingStore.getState().attempt;
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_input, options) => new Promise<Response>((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason));
    })));
    try {
      const queueRead = fetchAndInitializeQueue();
      const rejected = expect(queueRead).rejects.toThrow(/timed out/i);
      await vi.advanceTimersByTimeAsync(15_000);
      await rejected;
      expect(useTrainingStore.getState().attempt).toEqual(completedAttempt);
      expect(useTrainingStore.getState().serviceError).toContain("Retry loading the queue");
    } finally {
      vi.useRealTimers();
    }
  });
  it("readable renamed store fields retain local authority, failure, and sound through Home selectors", () => {
    const store = useTrainingStore.getState();
    store.setDatabaseQueue(true);
    store.setAttemptFailed(true);
    store.setSoundOn(false);
    const view = selectHomeViewState(useTrainingStore.getState());
    expect(view.databaseQueue).toBe(true);
    expect(view.attemptFailed).toBe(true);
    expect(view.soundOn).toBe(false);
  });

  it("previously studied unassisted card is playable after atomic queue hydration and refresh", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    expect(selectTrainingViewState(useTrainingStore.getState()).isLocked).toBe(
      false,
    );
    expect(useTrainingStore.getState().showHint).toBe(false);
    store.hydrateLocalQueue([card]);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
    expect(selectTrainingViewState(useTrainingStore.getState()).isLocked).toBe(
      false,
    );
  });

  it("same-entry queue refresh preserves position and an active reply transition", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    store.setStep(1);
    store.setAttemptPhase("opponentReplyPending");
    const token = useTrainingStore.getState().attempt;
    store.hydrateLocalQueue([card]);
    expect(useTrainingStore.getState().step).toBe(1);
    expect(useTrainingStore.getState().attempt).toEqual(token);
  });

  it("stale opponent replies and completion timers cannot lock or complete a replacement queue entry", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    const token = useTrainingStore.getState().attempt;
    const replacement = {
      ...card,
      queueEntryId: asQueueEntryId(43),
      queueCycle: 1,
      queueAttemptState: "reinforcement" as const,
    };
    store.hydrateLocalQueue([replacement], true);
    store.setAttemptPhase("complete", token);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
    expect(selectTrainingViewState(useTrainingStore.getState()).isLocked).toBe(
      false,
    );
  });

  it("queue refresh during completed tactic feedback preserves the final position and attempt token", () => {
    const store = useTrainingStore.getState();
    const tacticalCard: PracticeCard = {
      ...card,
      id: asCardId("tactic-review"),
      backendId: asCardId("tactic-backend"),
      queueEntryId: asQueueEntryId(77),
      kind: "puzzle",
      title: "Tactics review",
      startingFen: asFenString(
        "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17",
      ),
      orientation: "black",
      moves: [asSanMove("Kxf7"), asSanMove("Qf3+")],
      userMoveTarget: 1,
    };
    store.hydrateLocalQueue([tacticalCard], true);
    useTrainingStore.setState((state) => ({
      ...state,
      attempt: {
        entryKey: attemptEntryKey(tacticalCard),
        generation: state.attempt.generation,
        phase: "feedbackPause",
      },
      currentFenString: asFenString(new Chess().fen()),
      feedback: "complete",
    }));
    const completedToken = useTrainingStore.getState().attempt;
    store.hydrateLocalQueue([tacticalCard]);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.attempt).toEqual(completedToken);
    expect(refreshed.currentFenString).not.toBe(tacticalCard.startingFen);
    expect(selectTrainingViewState(refreshed).isLocked).toBe(true);
  });

  it("queue refresh removing a completed tactic retains its final board until grading", () => {
    const store = useTrainingStore.getState();
    const nextCard = { ...card, id: asCardId("next"), queueEntryId: asQueueEntryId(43) };
    store.hydrateLocalQueue([card], true, 2);
    store.setStep(card.moves.length);
    store.setFeedback("complete");
    store.setAttemptPhase("feedbackPause");
    const completed = useTrainingStore.getState();
    store.hydrateLocalQueue([nextCard], false, 1);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.getCard().queueEntryId).toBe(card.queueEntryId);
    expect(refreshed.attempt).toEqual(completed.attempt);
    expect(refreshed.step).toBe(completed.step);
    expect(refreshed.practiceCards[1].queueEntryId).toBe(nextCard.queueEntryId);
  });
});
