import { Chess } from "chess.js";
import { useState } from "react";
import { historyKeyboardActions } from "../lib/keyboard-shortcuts";

// A view cursor never mutates the authoritative attempt or creates future moves.
export function useBoardHistory(positionKey: string, positions: readonly string[], liveFen: string) {
  const [browsing, setBrowsing] = useState<{ key: string; cursor: number } | null>(null);
  const historical = positions.length > 0 && browsing?.key === positionKey;
  const lastCursor = Math.max(0, positions.length - 1);
  const matchingLiveCursor = positions.findLastIndex(fen => fen === liveFen);
  // An off-line live answer is outside history. Previous enters at its frontier;
  // every revealed cursor stays read-only until reset returns to that answer.
  const liveCursor = matchingLiveCursor >= 0 ? matchingLiveCursor : null;
  const cursor = historical ? Math.min(browsing.cursor, lastCursor) : liveCursor ?? positions.length;
  const viewingHistory = Boolean(historical && (cursor !== liveCursor || positions[cursor] !== liveFen));
  const navigate = (nextCursor: number) => {
    if (positions.length) setBrowsing(nextCursor === liveCursor ? null : { key: positionKey, cursor: nextCursor });
  };
  return {
    cursor,
    fen: viewingHistory ? positions[cursor] : liveFen,
    viewingHistory,
    keyboard: { ...historyKeyboardActions(cursor, lastCursor, navigate), reset: () => setBrowsing(null) },
  };
}

export function positionsFromMoves(startingFen: string, moves: readonly string[], revealedLength = moves.length) {
  const board = new Chess(startingFen);
  const positions = [startingFen];
  for (const move of moves.slice(0, revealedLength)) {
    try { board.move(move); positions.push(board.fen()); }
    catch { break; }
  }
  return positions;
}
