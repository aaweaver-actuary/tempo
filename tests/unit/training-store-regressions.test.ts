import { describe, expect, it } from "vitest";
import { useTrainingStore } from "../../app/state/training-store";
import {
  asCardId,
  asFenString,
  asSanMove,
  type PracticeCard,
} from "../../app/types";

describe("training store", () => {
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
      currentFenString: "",
      step: 999,
      feedback: "wrong",
      lastMove: ["a2", "a3"],
      opponentLastMove: ["a7", "a6"],
      isLocked: true,
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
    expect(state.isLocked).toBe(false);
  });
});
