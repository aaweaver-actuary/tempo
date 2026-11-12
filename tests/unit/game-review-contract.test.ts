import { expect, it } from "vitest";
import { Chess } from "chess.js";
import { decodeGameReview } from "../../app/features/game-review/contracts";

const board = new Chess();
const initial = board.fen();
board.move("e4");
const next = board.fen();
const base = { starting_fen: initial, positions: [initial, next], moves: ["e2e4"] };

it("decodes the current comparison variants and saved orientation", () => {
  for (const comparison of [
    { kind: "covered" }, { kind: "unavailable", reason: "No line" },
    { kind: "coverage_end", ply: 1, fen: initial },
    { kind: "departure", ply: 1, fen: initial, played_uci: "e2e4", expected_uci: ["d2d4"], actor: "white" },
  ]) expect(decodeGameReview({ ...base, comparison }).comparison).toEqual(comparison);
  expect(decodeGameReview({ ...base, comparison: { kind: "covered" }, color: "black" }, true).color).toBe("black");
});

it("rejects malformed positions, illegal or inconsistent moves, colors and comparison bounds", () => {
  const valid = { ...base, comparison: { kind: "covered" } };
  for (const data of [
    { ...valid, positions: [] }, { ...valid, positions: ["bad", next] },
    { ...valid, positions: [initial] }, { ...valid, positions: [initial, initial] },
    { ...valid, moves: ["e2e5"] }, { ...valid, moves: ["nonsense"] },
    { ...valid, color: "blue" }, { ...valid, comparison: { kind: "matched" } },
    { ...valid, comparison: { kind: "coverage_end", ply: 2, fen: initial } },
    { ...valid, comparison: { kind: "departure", ply: 0, fen: initial } },
    { ...valid, comparison: { kind: "departure", ply: 1, fen: initial, played_uci: "e2e4", expected_uci: ["d2d4"], actor: "black" } },
  ]) expect(() => decodeGameReview(data)).toThrow(/Invalid Game Review/);
  expect(() => decodeGameReview(valid, true)).toThrow(/Invalid Game Review/);
});
