import { beforeEach, describe, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import {
  useTrainingStore,
  selectHomeViewState,
  selectTrainingViewState,
} from "../../app/state/training-store";
import { attemptEntryKey } from "../../app/domain/attempt";
import { conflictedReviews, enqueuePendingReview, pendingReviews } from "../../app/lib/review-outbox";
import { enqueueTrainingFailure, pendingTrainingFailures } from "../../app/lib/training-failure-outbox";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
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

beforeEach(() => {
  localStorage.clear();
  clearDebugErrors();
  useTrainingStore.getState().setPendingReviewError("");
  useTrainingStore.getState().initializeCardState(card);
});

describe("review attempt reliability", () => {
  it("refresh removing an active opening preserves its board and attempt for completion", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true, 1);
    store.setStep(1);
    store.setCurrentFenString(asFenString(new Chess().fen()));
    const before = useTrainingStore.getState();
    const replacement = { ...card, id: asCardId("future-card"), backendId: asCardId("future-card"), queueEntryId: asQueueEntryId(43) };
    store.hydrateLocalQueue([replacement], false, 1);
    const after = useTrainingStore.getState();
    expect(after.attempt).toEqual(before.attempt);
    expect(after.currentFenString).toBe(before.currentFenString);
    expect(after.getCard().queueEntryId).toBe(42);
    expect(after.practiceCards[1].queueEntryId).toBe(43);
    expect(after.serviceError).toBe("");
  });
  it.each(["playerTurn", "opponentReplyPending", "guided", "feedbackPause"] as const)("refresh preserves active %s state and identity when the queue projection disappears", (phase) => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true, 1);
    store.setAttemptPhase(phase);
    const before = useTrainingStore.getState();
    store.hydrateLocalQueue([], false, 0);
    const after = useTrainingStore.getState();
    expect(after.attempt).toEqual(before.attempt);
    expect(after.attempt.attemptId).toBeTruthy();
    expect(after.currentFenString).toBe(before.currentFenString);
    expect(after.getCard()).toEqual(card);
    expect(after.cardsLeft).toBe(1);
  });
  it("reload drains an earlier review before marking the next guided card", async () => {
    enqueuePendingReview({ backendId: "persisted-card", queueEntryId: 42,
      outcome: "correct", guided: false });
    enqueueTrainingFailure(43, "next-card", 1);
    const requestedPaths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname;
      requestedPaths.push(path);
      if (path.endsWith("/review")) return Response.json({ persisted: true });
      if (path.endsWith("/fail")) return Response.json({ attempt_failed: true });
      return Response.json({ cards: [{
        id: "next-card", queue_entry_id: 43, start_fen: card.startingFen,
        moves: ["d2d4"], content_type: "opening", repertoire_name: "Next guided card",
        repertoire_source: "PGN",
      }], count: 1 });
    }));
    await fetchAndInitializeQueue();
    expect(requestedPaths.slice(0, 3)).toEqual([
      "/api/cards/persisted-card/review", "/api/queue/entries/43/fail", "/api/queue/window",
    ]);
    expect(pendingReviews()).toEqual([]);
    expect(useTrainingStore.getState().getCard().queueEntryId).toBe(43);
    expect(useTrainingStore.getState().isAttemptFailed).toBe(true);
    await vi.waitFor(() => expect(pendingTrainingFailures()).toEqual([]));
  });
  it("desktop queue failure retains the active attempt and attributes the live request", async () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true, 1);
    store.setStep(1);
    const activeFen = useTrainingStore.getState().currentFenString;
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Load failed")));
    await expect(fetchAndInitializeQueue()).rejects.toThrow("Load failed");
    expect(useTrainingStore.getState().currentFenString).toBe(activeFen);
    expect(useTrainingStore.getState().step).toBe(1);
    expect(useTrainingStore.getState().isOfflineQueueActive).toBe(false);
    expect(useTrainingStore.getState().serviceError).toContain("Load failed");
    expect(debugErrors().at(-1)?.context).toMatchObject({ source: "training-queue", endpointPath: "/api/queue/window" });
  });
  it("stale guided failure replay completes before today's training queue opens", async () => {
    enqueuePendingReview({ backendId: "persisted-card", queueEntryId: 42, outcome: "correct", guided: true });
    const requestedPaths: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      requestedPaths.push(url);
      if (url.endsWith("/fail"))
        return Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 });
      if (url.endsWith("/review")) return Response.json({ persisted: true });
      return Response.json({ cards: [{
        id: "other-card", queue_entry_id: 43, start_fen: card.startingFen,
        moves: ["d2d4"], content_type: "opening", repertoire_name: "Available", repertoire_source: "PGN",
      }], count: 1 });
    }));

    await fetchAndInitializeQueue();

    expect(requestedPaths.map((url) => new URL(url).pathname)).toEqual([
      "/api/queue/entries/42/fail", "/api/cards/persisted-card/review", "/api/queue/window",
    ]);
    expect(pendingReviews()).toEqual([]);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(43);
    expect(useTrainingStore.getState().pendingReviewError).toBe("");
    expect(useTrainingStore.getState().serviceError).toBe("");
  });

  it("reload retains an unprovable result as a conflict and opens independent cards", async () => {
    localStorage.clear();
    enqueuePendingReview({ backendId: "persisted-card", queueEntryId: 42, outcome: "correct", guided: true });
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/fail"))
        return Response.json({ detail: "This queue attempt is no longer active" }, { status: 409 });
      if (url.endsWith("/review"))
        return Response.json({ detail: "This queue attempt is no longer available" }, { status: 409 });
      if (url.endsWith("/review/reconcile"))
        return Response.json({ persisted: false, conflict: { code: "queue_attempt_unprovable", message: "Original attempt unavailable", retryable: false } });
      return Response.json({ cards: [
        { id: "persisted-card", queue_entry_id: 42, start_fen: card.startingFen,
          moves: ["e2e4"], content_type: "opening", repertoire_name: "Pending", repertoire_source: "PGN" },
        { id: "other-card", queue_entry_id: 43, start_fen: card.startingFen,
          moves: ["d2d4"], content_type: "opening", repertoire_name: "Available", repertoire_source: "PGN" },
      ], count: 2 });
    });
    vi.stubGlobal("fetch", fetchMock);

    await fetchAndInitializeQueue();

    expect(useTrainingStore.getState().practiceCards.map((queuedCard) => queuedCard.queueEntryId)).toEqual([43]);
    expect(useTrainingStore.getState().cardsLeft).toBe(1);
    expect(useTrainingStore.getState().serviceError).toBe("");
    expect(useTrainingStore.getState().pendingReviewError).toBe("");
    expect(pendingReviews()).toHaveLength(0);
    expect(conflictedReviews()).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/api/queue/window"))).toBe(true);
  });
  it("advances to a prefetched card before review persistence and retains the total queue count", () => {
    const nextCard = { ...card, id: asCardId("next-card"), queueEntryId: asQueueEntryId(43) };
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card, nextCard], true, 135);
    expect(useTrainingStore.getState().advanceCachedQueue()).toBe(true);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(43);
    expect(useTrainingStore.getState().cardsLeft).toBe(134);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
  });
  it("repeated refreshes retain an active attempt until explicit advancement", () => {
    const replacement = { ...card, id: asCardId("replacement"), queueEntryId: asQueueEntryId(44) };
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    store.setStep(1);
    store.hydrateLocalQueue([replacement]);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(42);
    expect(useTrainingStore.getState().attempt.phase).toBe("playerTurn");
    expect(useTrainingStore.getState().serviceError).toBe("");
    store.hydrateLocalQueue([replacement]);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(42);
    expect(store.advanceCachedQueue()).toBe(true);
    expect(useTrainingStore.getState().practiceCards[0].queueEntryId).toBe(44);
  });
  it("keeps an in-progress phone attempt while a 225-card queue refreshes to 241 cards", () => {
    const nextCard = { ...card, id: asCardId("new-first"), queueEntryId: asQueueEntryId(44) };
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true, 225);
    store.setStep(1);
    const attemptBeforeRefresh = useTrainingStore.getState().attempt;
    store.hydrateLocalQueue([nextCard, card], false, 241);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.cardsLeft).toBe(241);
    expect(refreshed.getCard().queueEntryId).toBe(42);
    expect(refreshed.step).toBe(1);
    expect(refreshed.attempt).toEqual(attemptBeforeRefresh);
  });
  it("same queue ID on replacement content cannot consume the active card's future slot", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    const beforeRefresh = useTrainingStore.getState();
    const replacement = { ...card, id: asCardId("replacement-card"), backendId: asCardId("replacement-card") };
    store.hydrateLocalQueue([replacement], false, 1);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.getCard()).toEqual(card);
    expect(refreshed.practiceCards[1]).toEqual(replacement);
    expect(refreshed.cardsLeft).toBe(2);
    expect(refreshed.attempt).toEqual(beforeRefresh.attempt);
  });
  it("unchanged active content refreshes its priority reason without resetting its board or logical attempt", () => {
    const store = useTrainingStore.getState();
    store.hydrateLocalQueue([card], true);
    store.setStep(1);
    const beforeRefresh = useTrainingStore.getState();
    store.hydrateLocalQueue([{ ...card, priorityReason: "Priority review · missed in a recent game" }]);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.getCard().priorityReason).toBe("Priority review · missed in a recent game");
    expect(refreshed.currentFenString).toBe(beforeRefresh.currentFenString);
    expect(refreshed.boardAttempt).toBe(beforeRefresh.boardAttempt);
    expect(refreshed.step).toBe(beforeRefresh.step);
    expect(refreshed.attempt).toEqual(beforeRefresh.attempt);
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
