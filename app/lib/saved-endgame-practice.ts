import { Chess } from "chess.js";
import type { PracticeCard } from "../types";

/** Only persisted legal solution lines qualify for ordinary saved-card practice. */
export function hasSavedEndgameLine(card: PracticeCard): boolean {
  if (card.kind !== "endgame" || card.moves.length === 0) return false;
  try {
    const board = new Chess(card.startingFen);
    for (const move of card.moves) if (!board.move(move)) return false;
    return true;
  } catch {
    return false;
  }
}
