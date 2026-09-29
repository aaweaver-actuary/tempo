// @vitest-environment node
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { createStudyPositionStore } from "../../app/lib/study-position-store";
import type { ComparisonCard, ComparisonPosition } from "../../app/lib/comparison";

const startFen = new Chess().fen();
const screenshotRoute = ["d2d4", "g8f6", "c1f4", "d7d5", "e2e3", "b8c6", "c2c4", "d5c4", "b1c3"];
const bishopRoute = ["d2d4", "d7d5", "c1f4", "e7e6", "e2e3", "b8c6", "c2c4", "d5c4", "f1c4"];
const transposedBishopRoute = ["d2d4", "e7e6", "c1f4", "d7d5", "e2e3", "b8c6", "c2c4", "d5c4", "f1c4"];

function savedCard(id: string, moves: string[]): ComparisonCard {
  return { id, start_fen: startFen, moves, kind: "prefix", state: "learning",
    trained_color: "white", repertoires: [{ id: "london", name: "London System" }] };
}

it("London comparison keeps Nc3 distinct from the nearby Bxc4 decision and retains transposed card routes", () => {
  const store = createStudyPositionStore();
  store({ kind: "initializeComparisonIndex", revision: 1, cards: [
    savedCard("screenshot", screenshotRoute),
    savedCard("bishop", bishopRoute),
    savedCard("bishop-transposition", transposedBishopRoute),
  ] });
  const board = new Chess();
  for (const uci of screenshotRoute.slice(0, 8)) board.move(uci);
  const matches = store({ kind: "findComparisonMatches", revision: 1,
    fen: board.fen(), repertoireId: "london" }) as ComparisonPosition[];
  const decisions = matches.filter((match) => match.ply === 8);
  expect(decisions.map(({ cardId, distance, nextUci }) => [cardId, distance, nextUci]))
    .toEqual([["screenshot", 0, "b1c3"], ["bishop", 2, "f1c4"],
      ["bishop-transposition", 2, "f1c4"]]);
  expect(decisions[1].fen.split(" ").slice(0, 4))
    .toEqual(decisions[2].fen.split(" ").slice(0, 4));
  expect(decisions[0].fen.split(" ").slice(0, 4))
    .not.toEqual(decisions[1].fen.split(" ").slice(0, 4));
});

it("comparison search excludes incompatible material and releases its index", () => {
  const store = createStudyPositionStore();
  store({ kind: "initializeComparisonIndex", revision: 3, cards: [
    savedCard("bishop", bishopRoute), savedCard("invalid", ["d2d4", "a1a8"]),
  ] });
  expect((store({ kind: "findComparisonMatches", revision: 3, fen: startFen }) as ComparisonPosition[])
    .some((position) => position.cardId === "invalid")).toBe(false);
  const board = new Chess();
  board.remove("a2");
  expect(store({ kind: "findComparisonMatches", revision: 3, fen: board.fen() }))
    .toEqual([]);
  expect(store({ kind: "releaseComparisonIndex", revision: 3 })).toBe(true);
  expect(() => store({ kind: "findComparisonMatches", revision: 3, fen: startFen }))
    .toThrow("Comparison index is unavailable");
});

it("stale comparison initialization cannot replace a newer card index", () => {
  const store = createStudyPositionStore();
  store({ kind: "initializeComparisonIndex", revision: 5, cards: [savedCard("current", screenshotRoute)] });
  store({ kind: "initializeComparisonIndex", revision: 4, cards: [savedCard("stale", bishopRoute)] });
  const matches = store({ kind: "findComparisonMatches", revision: 5, fen: startFen }) as ComparisonPosition[];
  expect(matches.length).toBeGreaterThan(0);
  expect(new Set(matches.map((match) => match.cardId))).toEqual(new Set(["current"]));
});
