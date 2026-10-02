import type { Page } from "@playwright/test";
import { Chess, type Square } from "chess.js";
import { test, expect } from "./observability";
import { prepareVisualUI } from "./visual-fixtures";
import { navigate } from "./ui-fixtures";

const findingFen = new Chess().fen();
const finding = { finding_id: "restoration-finding", kind: "repertoire lapse", ply: 0,
  fen: findingFen, motif: null, confidence: 1 };
const session = { id: "restoration-review", game_id: "visual-game", status: "active",
  current_index: 0, total: 1, current: finding, attempts: [] };

function expectedPieces(fen: string) {
  return new Chess(fen).board().flatMap((rank) => rank.flatMap((piece) => piece
    ? [`${piece.square}:${piece.color}:${piece.type}`] : [])).sort();
}

async function renderedPieces(page: Page) {
  // Read visible piece classes and geometry, rather than React's FEN attribute
  // or a production global exposing Chessground's internal state.
  return page.locator(".cg-wrap cg-board").evaluate((board) => {
    const bounds = board.getBoundingClientRect();
    const blackOrientation = board.closest(".cg-wrap")?.classList.contains("orientation-black");
    const pieceTypes: Record<string, string> = { pawn: "p", knight: "n", bishop: "b", rook: "r", queen: "q", king: "k" };
    return Array.from(board.querySelectorAll("piece:not(.ghost)")).map((piece) => {
      const pieceBounds = piece.getBoundingClientRect();
      const column = Math.floor((pieceBounds.x + pieceBounds.width / 2 - bounds.x) / (bounds.width / 8));
      const row = Math.floor((pieceBounds.y + pieceBounds.height / 2 - bounds.y) / (bounds.height / 8));
      const file = blackOrientation ? 7 - column : column;
      const rank = blackOrientation ? row + 1 : 8 - row;
      const role = Object.keys(pieceTypes).find((name) => piece.classList.contains(name));
      return `${String.fromCharCode(97 + file)}${rank}:${piece.classList.contains("white") ? "w" : "b"}:${role ? pieceTypes[role] : "unknown"}`;
    }).sort();
  });
}

async function clickSquare(page: Page, square: Square) {
  const bounds = (await page.locator(".cg-wrap cg-board").boundingBox())!;
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  await page.mouse.click(bounds.x + (file + 0.5) * bounds.width / 8,
    bounds.y + (7 - rank + 0.5) * bounds.height / 8);
}

for (const candidate of [
  { move: "e2e4", correct: true, feedback: "That correction works." },
  { move: "d2d4", correct: false, feedback: "There is a stronger correction." },
]) {
  test(`guided review restores the authoritative finding position after a same-FEN reveal (${candidate.correct ? "correct" : "incorrect"} candidate)`, async ({ page }) => {
    await prepareVisualUI(page);
    await page.route("**/api/games/visual-game/guided-review", (route) => route.fulfill({ json: session }));
    const submissions: unknown[] = [];
    let releaseReveal!: () => void;
    const revealReady = new Promise<void>((resolve) => { releaseReveal = resolve; });
    await page.route("**/api/guided-reviews/restoration-review/attempt", async (route) => {
      expect(route.request().method()).toBe("POST");
      submissions.push(route.request().postDataJSON());
      await revealReady;
      await route.fulfill({ json: {
        correct: candidate.correct,
        revealed: { ...finding, answer: { actual_move_uci: "d2d4", best_move_uci: "e2e4",
          expected_moves: ["e2e4"], loss_cp: 120, principal_variation: ["e4", "e5"] } },
        session: { ...session, status: "complete", current_index: 1, current: null,
          attempts: [{ finding_id: finding.finding_id, move_uci: candidate.move, correct: candidate.correct }] },
      } });
    });
    await navigate(page, "Games");
    await page.getByRole("button", { name: "Review this game" }).click();
    const board = page.locator(".board-frame");
    await expect(page.locator(".guided-game-review")).toContainText("Play it on the board.");
    await expect(board).toHaveAttribute("data-input-enabled", "true");
    await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(findingFen));
    await page.keyboard.press("ArrowRight"); await page.keyboard.press("End");
    await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(findingFen));
    expect(submissions).toEqual([]);
    try {
      await clickSquare(page, candidate.move.slice(0, 2) as Square);
      await clickSquare(page, candidate.move.slice(2, 4) as Square);
      const appliedCandidate = new Chess(findingFen);
      appliedCandidate.move({ from: candidate.move.slice(0, 2), to: candidate.move.slice(2, 4) });
      await expect.poll(() => submissions).toEqual([{ move_uci: candidate.move }]);
      await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(appliedCandidate.fen()));
      await expect(board).toHaveAttribute("data-input-enabled", "true");
    } finally {
      releaseReveal();
    }
    await expect(page.locator(".guided-game-review")).toContainText(candidate.feedback);
    await expect(board).toHaveAttribute("data-input-enabled", "false");
    await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(findingFen));
    await page.keyboard.press("End");
    const revealedEnd = new Chess(findingFen); revealedEnd.move("e4"); revealedEnd.move("e5");
    await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(revealedEnd.fen()));
    await page.keyboard.press("r");
    await expect.poll(() => renderedPieces(page)).toEqual(expectedPieces(findingFen));
    expect(submissions).toEqual([{ move_uci: candidate.move }]);
  });
}
