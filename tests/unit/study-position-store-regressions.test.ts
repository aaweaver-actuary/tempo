// @vitest-environment node
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { createStudyPositionStore } from "../../app/lib/study-position-store";
import { computeStudyTask } from "../../app/lib/study-computation";
import type { AnalysisLine } from "../../app/types";
import { canonicalizeLine } from "../../app/utils/canonical-line";
import { studyRequestSchema } from "../../app/domain/schemas";
import type { IndexedPosition } from "../../app/lib/position-similarity";

it("worker position index retains parity and rejects stale revisions", () => {
  const run = createStudyPositionStore();
  const line = {
    id: "line-one", repertoireId: "white-one", repertoireName: "White", title: "One",
    side: "white", startingFen: new Chess().fen(), moves: ["e2e4", "e7e5"],
  } as AnalysisLine;
  const canonicalLine = canonicalizeLine(line);
  const positions = computeStudyTask({ kind: "index", lines: [line] }) as IndexedPosition[];
  const expected = computeStudyTask({ kind: "matches", fen: line.startingFen, positions });
  expect(run({ kind: "initializePositionIndex", repertoireId: line.repertoireId, revision: 1, lines: [canonicalLine] }))
    .toEqual({ revision: 1, indexedPositions: 3 });
  expect(run({ kind: "findPositionMatches", repertoireId: line.repertoireId, revision: 1, fen: line.startingFen }))
    .toEqual(expected);
  expect(run({ kind: "initializePositionIndex", repertoireId: line.repertoireId, revision: 2, lines: [canonicalLine] }))
    .toEqual({ revision: 2, indexedPositions: 3 });
  expect(run({ kind: "releasePositionIndex", repertoireId: line.repertoireId, revision: 1 })).toBe(false);
  expect(() => run({ kind: "findPositionMatches", repertoireId: line.repertoireId, revision: 1, fen: line.startingFen }))
    .toThrow("unavailable");
  expect(run({ kind: "releasePositionIndex", repertoireId: line.repertoireId, revision: 2 })).toBe(true);
  expect(() => run({ kind: "findPositionMatches", repertoireId: line.repertoireId, revision: 2, fen: line.startingFen }))
    .toThrow("unavailable");
});

it("worker index protocol accepts canonical lines and rejects raw lines", () => {
  const rawLine = {
    id: "line-two", repertoireId: "white-two", repertoireName: "White", title: "Two",
    side: "white", startingFen: new Chess().fen(), moves: ["d2d4", "d7d5"],
  } as AnalysisLine;
  const request = (lines: unknown[]) => ({
    id: 1,
    task: { kind: "initializePositionIndex", repertoireId: "white-two", revision: 1, lines },
  });
  expect(studyRequestSchema.safeParse(request([rawLine])).success).toBe(false);
  expect(studyRequestSchema.safeParse(request([canonicalizeLine(rawLine)])).success).toBe(true);
});
