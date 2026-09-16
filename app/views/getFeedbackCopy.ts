import { PracticeCard } from "../types";
import { trainedColor } from "../utils/cards";

export function getFeedbackCopy(isAttemptFailed: boolean, card: PracticeCard) {
  return {
    ready: {
      title: "Your move",
      body:
        card.kind === "puzzle"
          ? "Find the strongest continuation."
          : `Continue the line for ${trainedColor(card) === "white" ? "White" : "Black"}.`,
    },
    correct: {
      title: "That's it",
      body: `${trainedColor(card) === "white" ? "Black" : "White"} is replying…`,
    },
    branch: {
      title: "Also in your repertoire",
      body: "That move is valid. Replay the arrowed move for the branch being tested.",
    },
    wrong: {
      title: "Try that position again",
      body: "That move is legal, but it isn't in this repertoire.",
    },
    complete: {
      title: isAttemptFailed ? "Guided line complete" : "Line recalled",
      body: "",
    },
  };
}
