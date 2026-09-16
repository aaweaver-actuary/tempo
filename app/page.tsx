"use client";

import { Chess } from "chess.js";
import type { PracticeCard } from "./types";
import { trainedColor } from "./utils/cards";

export interface TrainingState {
  fen: string;
  step: number;
  lastMove: [string, string] | undefined;
}

// Returns the initial training state for a given practice card, including the FEN string,
// the current step, and the last move if applicable.
export function initialTrainingState(card: PracticeCard): TrainingState {
  const position = new Chess(card.startingFen);
  const turn = position.turn() === "b" ? "black" : "white";
  if (card.kind === "opening" && turn !== trainedColor(card) && card.moves[0]) {
    const move = position.move(card.moves[0]);
    return {
      fen: position.fen(),
      step: 1,
      lastMove: [move.from, move.to] as [string, string],
    };
  }
  return { fen: card.startingFen, step: 0, lastMove: undefined };
}

// Returns the subset of moves whose cumulative weight does not exceed the specified target.
export function candidatesAboveThreshold<T>(
  moves: T[],
  weight: (move: T) => number,
  target_percentage: number,
) {
  const total = moves.reduce((sum, move) => sum + weight(move), 0);
  let cumulative = 0;
  return moves.filter((move) => {
    if (total === 0 || cumulative / total >= target_percentage) return false;
    cumulative += weight(move);
    return true;
  });
}
