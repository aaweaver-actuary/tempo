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

  it("same-entry tactical refresh clears stale feedback pause so the board stays playable", () => {
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
    store.hydrateLocalQueue([tacticalCard]);
    const refreshed = useTrainingStore.getState();
    expect(refreshed.attempt.phase).toBe("playerTurn");
    expect(refreshed.currentFenString).toBe(tacticalCard.startingFen);
    expect(selectTrainingViewState(refreshed).isLocked).toBe(false);
  });
});
