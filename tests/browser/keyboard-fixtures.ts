import { expect, type Locator, type Page } from "@playwright/test";
import { Chess } from "chess.js";

export function expectedPieces(fen: string) {
  return new Chess(fen).board().flatMap(rank => rank.flatMap(piece => piece
    ? [`${piece.square}:${piece.color}:${piece.type}`] : [])).sort();
}

export async function renderedPieces(board: Locator) {
  return board.locator("cg-board").evaluate(surface => {
    const bounds = surface.getBoundingClientRect();
    const black = surface.closest(".cg-wrap")?.classList.contains("orientation-black");
    const pieceTypes: Record<string, string> = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };
    return [...surface.querySelectorAll("piece:not(.ghost)")].map(piece => {
      const pieceBounds = piece.getBoundingClientRect();
      const column = Math.floor((pieceBounds.x + pieceBounds.width / 2 - bounds.x) / (bounds.width / 8));
      const row = Math.floor((pieceBounds.y + pieceBounds.height / 2 - bounds.y) / (bounds.height / 8));
      const role = Object.keys(pieceTypes).find(name => piece.classList.contains(name));
      return `${String.fromCharCode(97 + (black ? 7 - column : column))}${black ? row + 1 : 8 - row}:${piece.classList.contains("white") ? "w" : "b"}:${role ? pieceTypes[role] : "unknown"}`;
    }).sort();
  });
}

export async function squareCenter(board: Locator, square: string) {
  const bounds = (await board.locator("cg-board").boundingBox())!;
  const black = await board.getAttribute("data-orientation") === "black";
  const file = square.charCodeAt(0) - 97, rank = Number(square[1]) - 1;
  return { x: bounds.x + ((black ? 7 - file : file) + 0.5) * bounds.width / 8,
    y: bounds.y + ((black ? rank : 7 - rank) + 0.5) * bounds.height / 8 };
}
export async function playMove(page: Page, board: Locator, from: string, to: string) {
  await board.scrollIntoViewIfNeeded();
  const positionBeforeMove = await board.getAttribute("data-fen");
  for (const square of [from, to]) { const center = await squareCenter(board, square); await page.mouse.click(center.x, center.y); }
  // Chessground defers its move callback. Subsequent keyboard/button actions
  // must observe the application position, rather than just moved DOM pieces.
  await expect(board).not.toHaveAttribute("data-fen", positionBeforeMove!);
}
