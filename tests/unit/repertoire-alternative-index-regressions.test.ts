// @vitest-environment node
import { Chess } from "chess.js";
import { afterEach, expect, it, vi } from "vitest";
import { asFenString, asLineId, asUciMove, type AnalysisLine, type RepertoireId } from "../../app/types";
import * as canonical from "../../app/utils/canonical-line";
import { computeStudyTask } from "../../app/lib/study-computation";
import type { IndexedPosition } from "../../app/lib/position-similarity";
import { buildRepertoireAlternativeSnapshot, lookupRepertoireAlternative } from "../../app/lib/repertoire-alternative-index";
import { studyRequestSchema } from "../../app/domain/schemas";
import { createStudyPositionStore } from "../../app/lib/study-position-store";

const repertoireId = "white" as RepertoireId;
const startFen = new Chess().fen();
function legacyScan(positions: readonly IndexedPosition[], repertoire: string | undefined, fen: string, uci: string) {
  return positions.some(position => position.repertoireId === repertoire &&
    canonical.canonicalFenKey(position.fen) === canonical.canonicalFenKey(fen) && position.nextUci === uci);
}
function line(id: string, moves: string[], startingFen = startFen, repertoire = repertoireId): AnalysisLine {
  return { id: asLineId(id), repertoireId: repertoire, repertoireName: repertoire, title: id,
    side: "white", startingFen: asFenString(startingFen), moves: moves.map(asUciMove) };
}
function preparedPositions(lines: AnalysisLine[]) {
  return computeStudyTask({ kind: "index", lines }) as IndexedPosition[];
}
afterEach(() => vi.restoreAllMocks());

it("issue36_lookup_matches_legacy_scan", () => {
  const lines = [
    line("transpose-a", ["g1f3", "g8f6", "b1c3", "b8c6", "e2e4"]),
    line("transpose-b", ["b1c3", "b8c6", "g1f3", "g8f6", "d2d4"]),
    line("castle", ["e1g1"], "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"),
    line("no-castle", ["a1a2"], "r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1"),
    line("en-passant", ["e5f6"], "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"),
    line("no-en-passant", ["e5e6"], "rnbqkbnr/ppp1p1pp/8/3pPp2/8/8/PPPP1PPP/RNBQKBNR w KQkq - 0 3"),
    line("uncapturable-en-passant", ["e7e5"], "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"),
    line("promote-queen", ["a7a8q"], "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"),
    line("promote-knight", ["a7a8n"], "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"),
    line("empty", []),
    line("checkmate", [], "7k/6Q1/5K2/8/8/8/8/8 b - - 0 1"),
    line("stalemate", [], "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"),
  ];
  expect(new Chess(lines.at(-2)!.startingFen).isCheckmate()).toBe(true);
  expect(new Chess(lines.at(-1)!.startingFen).isStalemate()).toBe(true);
  let randomState = 36;
  for (let gameNumber = 0; gameNumber < 12; gameNumber++) {
    const board = new Chess();
    const moves: string[] = [];
    for (let ply = 0; ply < 12 && !board.isGameOver(); ply++) {
      randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0;
      const choices = board.moves({ verbose: true });
      const move = choices[randomState % choices.length];
      moves.push(`${move.from}${move.to}${move.promotion ?? ""}`);
      board.move(move);
    }
    lines.push(line(`generated-${gameNumber}`, moves));
  }
  const positions = preparedPositions(lines);
  positions.push({ ...positions[0] }, { ...positions[0], fen: `  ${positions[0].fen.replace(/\s+/g, "\t")}  ` });
  const originalRecords = structuredClone(positions);
  const snapshot = buildRepertoireAlternativeSnapshot(1, positions);
  expect(snapshot.positions).toBe(positions);
  expect(positions).toEqual(originalRecords);
  for (const position of positions) {
    const clocksChanged = `${canonical.canonicalFenKey(position.fen)} 17 42`;
    for (const fen of [position.fen, clocksChanged]) {
      for (const uci of [position.nextUci ?? "a2a3", "d2d4", "a7a8q", "a7a8n", "e1g1", "e5f6", "h2h4"])
        expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, fen, uci))
          .toBe(legacyScan(positions, repertoireId, fen, uci));
    }
  }
  const transposition = positions.find(position => position.lineId === "transpose-a" && position.ply === 4)!;
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, transposition.fen, "e2e4")).toBe(true);
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, transposition.fen, "d2d4")).toBe(true);
  // Exact four-field identity, rather than a new Chess-based query normalizer.
  const rawEnPassant = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, rawEnPassant, "e7e5")).toBe(false);
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, new Chess(rawEnPassant).fen(), "e7e5")).toBe(true);
});

it("issue36_repertoire_membership_is_isolated", () => {
  const otherRepertoire = "black" as RepertoireId;
  const positions = preparedPositions([line("white", ["e2e4"]), line("black", ["d2d4"], startFen, otherRepertoire)]);
  const snapshot = buildRepertoireAlternativeSnapshot(2, positions);
  expect(lookupRepertoireAlternative(snapshot, 2, repertoireId, startFen, "e2e4")).toBe(true);
  expect(lookupRepertoireAlternative(snapshot, 2, repertoireId, startFen, "d2d4")).toBe(false);
  expect(lookupRepertoireAlternative(snapshot, 2, otherRepertoire, startFen, "d2d4")).toBe(true);
  expect(lookupRepertoireAlternative(snapshot, 2, undefined, startFen, "e2e4")).toBe(false);
});

it("issue36_missing_stale_and_empty_snapshots_distinguish_unavailable_evidence", () => {
  const empty = buildRepertoireAlternativeSnapshot(2, []);
  expect(lookupRepertoireAlternative(null, 2, repertoireId, startFen, "e2e4")).toBeUndefined();
  expect(lookupRepertoireAlternative(empty, 3, repertoireId, startFen, "e2e4")).toBeUndefined();
  expect(lookupRepertoireAlternative(empty, 2, repertoireId, startFen, "e2e4")).toBe(false);
  const duplicates = preparedPositions([line("first", ["e2e4"]), line("second", ["e2e4"])]);
  const snapshot = buildRepertoireAlternativeSnapshot(3, duplicates);
  expect(snapshot.positions).toHaveLength(4);
  expect(snapshot.movesByRepertoire.get(repertoireId)?.get(canonical.canonicalFenKey(startFen) as import("../../app/types").FenKey)?.size).toBe(1);
});

it("issue36_existing_study_preparation_returns_one_cloneable_generation_snapshot", () => {
  const lines = [line("first", ["e2e4"]), line("second", ["d2d4"])];
  const task = { kind: "repertoireAlternatives" as const, generation: 36, lines };
  const parsed = studyRequestSchema.parse({ id: 1, task });
  const result = createStudyPositionStore()(parsed.task) as ReturnType<typeof buildRepertoireAlternativeSnapshot>;
  expect(result.positions).toEqual(preparedPositions(lines));
  const transferred = structuredClone(result);
  expect(lookupRepertoireAlternative(transferred, 36, repertoireId, startFen, "d2d4")).toBe(true);
  expect(lookupRepertoireAlternative(transferred, 35, repertoireId, startFen, "d2d4")).toBeUndefined();
  expect(studyRequestSchema.safeParse({ id: 1, task: { ...task, generation: 0 } }).success).toBe(false);
  expect(studyRequestSchema.safeParse({ id: 1, task: { ...task, generation: 1.5 } }).success).toBe(false);
});

it.each([1_000, 10_000, 100_000])("issue36_query_work_is_independent_of_record_count (%i)", recordCount => {
  const position: IndexedPosition = { lineId: "one", repertoireId, repertoireName: "White", fen: startFen, ply: 0, nextUci: "e2e4" };
  const positions = Array.from({ length: recordCount }, () => ({ ...position }));
  const snapshot = buildRepertoireAlternativeSnapshot(1, positions);
  const canonicalize = vi.spyOn(canonical, "canonicalFenKey");
  const getRepertoire = vi.spyOn(snapshot.movesByRepertoire, "get");
  const positionsByFen = snapshot.movesByRepertoire.get(repertoireId)!;
  const getPosition = vi.spyOn(positionsByFen, "get");
  const hasMove = vi.spyOn(positionsByFen.get(canonical.canonicalFenKey(startFen) as import("../../app/types").FenKey)!, "has");
  vi.clearAllMocks();
  // Make any accidental read of original records fail during the hot lookup.
  Object.defineProperty(snapshot, "positions", { get: () => { throw new Error("Query traversed source records"); } });
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, startFen, "e2e4")).toBe(true);
  expect(canonicalize).toHaveBeenCalledOnce();
  expect(getRepertoire).toHaveBeenCalledOnce();
  expect(getPosition).toHaveBeenCalledOnce();
  expect(hasMove).toHaveBeenCalledOnce();
  vi.clearAllMocks();
  expect(lookupRepertoireAlternative(snapshot, 1, repertoireId, startFen, "d2d4")).toBe(false);
  expect(canonicalize).toHaveBeenCalledOnce();
  expect(hasMove).toHaveBeenCalledOnce();
  vi.clearAllMocks();
  expect(legacyScan(positions, repertoireId, startFen, "d2d4")).toBe(false);
  expect(canonicalize).toHaveBeenCalledTimes(2 * recordCount);
});

it("issue190_complete_bulk_pipeline_preserves_snapshot_and_diagnostics_parity", () => {
  const payload = { lines: [{ id: "parity", repertoire_id: "parity", repertoire_name: "Parity",
    name: "Parity", trained_color: "white", start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4", "e7e5"] }] };
  const adapted = computeStudyTask({ kind: "transportLines", payload }) as import("../../app/types").AnalysisLine[];
  expect(computeStudyTask({ kind: "repertoirePreparation", generation: 190, payload }))
    .toEqual(computeStudyTask({ kind: "repertoireAlternatives", generation: 190, lines: adapted }));
});
