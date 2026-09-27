// @vitest-environment node
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { computeStudyTask } from "../../app/lib/study-computation";
import { chessPositionDistance } from "../../app/lib/position-similarity";

it("position search retains distance ordering and first duplicate identity", () => {
  const startFen = new Chess().fen();
  const afterKnights = new Chess();
  afterKnights.move("Nf3");
  afterKnights.move("Nf6");
  const nearFen = afterKnights.fen();
  expect(chessPositionDistance(startFen, nearFen)).toBe(2);
  expect(chessPositionDistance("invalid", startFen)).toBeUndefined();
  expect(chessPositionDistance(startFen, "invalid")).toBeUndefined();
  const position = (lineId: string, fen: string, nextUci: string) => ({
    lineId, repertoireId: "white", repertoireName: "White", fen, ply: 2, nextUci,
  });
  const positions = [
    position("near-first", nearFen, "d2d4"),
    position("exact-first", startFen, "e2e4"),
    position("exact-duplicate", startFen, "e2e4"),
    position("near-second", nearFen, "e2e4"),
  ];
  const matches = computeStudyTask({ kind: "matches", fen: startFen, positions }) as Array<{
    lineId: string; distance: number;
  }>;
  expect(matches.map(({ lineId, distance }) => [lineId, distance])).toEqual([
    ["exact-first", 0],
    ["near-first", 2],
    ["near-second", 2],
  ]);
});

it("repeated repertoire FENs retain match ordering and distinct next moves", () => {
  const exactFen = new Chess().fen();
  const positions = Array.from({ length: 100 }, (_, index) => ({
    lineId: `shared-${index}`,
    repertoireId: "white",
    repertoireName: "White",
    fen: exactFen,
    ply: index,
    nextUci: index === 99 ? "d2d4" : "e2e4",
  }));
  const matches = computeStudyTask({ kind: "matches", fen: exactFen, positions }) as Array<{
    lineId: string; nextUci: string; distance: number;
  }>;
  expect(matches.map(({ lineId, nextUci, distance }) => [lineId, nextUci, distance])).toEqual([
    ["shared-0", "e2e4", 0],
    ["shared-99", "d2d4", 0],
  ]);
});
