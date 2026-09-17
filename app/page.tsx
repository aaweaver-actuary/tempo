"use client";

export { initialTrainingState } from "./domain/attempt";

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
