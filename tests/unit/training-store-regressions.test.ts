// @vitest-environment node
import { describe, expect, it } from "vitest";
import { useTrainingStore } from "../../app/state/training-store";
import { buryQueuedCard } from "../../app/domain/training-session";
import {
  asCardId,
  asFenString,
  asSanMove,
  type PracticeCard,
} from "../../app/types";

describe("training store", () => {
  it("bury removes every occurrence of the active card and allows burying the last card", () => {
    const queue = [4, 8, 4, 12, 16];
    expect(buryQueuedCard(queue, 4)).toEqual([8, 12, 16]);
    expect(queue).toEqual([4, 8, 4, 12, 16]);
    expect(buryQueuedCard([4], 4)).toEqual([]);
    expect(buryQueuedCard(queue, 99)).toBe(queue);
  });

  it("resets the current card to the correct opening start state", () => {
    const card: PracticeCard = {
      id: asCardId("opening-1"),
      kind: "opening",
      title: "Queen's Gambit",
      subtitle: "Review the opening structure",
      startingFen: asFenString(
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      ),
      moves: [asSanMove("d4"), asSanMove("d5"), asSanMove("c4")],
      userMoveTarget: 0,
      orientation: "white",
    };

    useTrainingStore.setState({
      practiceCards: [card],
      activeCardIndex: 0,
      currentFenString: card.startingFen,
      step: 999,
      feedback: "wrong",
      lastMove: ["a2", "a3"],
      opponentLastMove: ["a7", "a6"],
      attempt: {entryKey:"old",generation:0,phase:"feedbackPause"},
    });

    useTrainingStore.getState().resetTrainingLine(card);

    const state = useTrainingStore.getState();
    expect(state.currentFenString).toBe(
      "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    );
    expect(state.step).toBe(0);
    expect(state.feedback).toBe("ready");
    expect(state.lastMove).toBeUndefined();
    expect(state.opponentLastMove).toBeUndefined();
    expect(state.attempt.phase).toBe("playerTurn");
  });
});
