// @vitest-environment node
import { Chess } from "chess.js";
import { readFileSync } from "node:fs";
import { expect, it, vi } from "vitest";
import type { AnalysisLine } from "../../app/types";
import { computeStudyTask } from "../../app/lib/study-computation";
import { chessPositionDistance, indexRepertoirePositions } from "../../app/lib/position-similarity";

it("TypeScript position distance matches the shared Rust parity fixture", () => {
  const fixturePath = new URL("../fixtures/position-distance-parity.json", import.meta.url);
  const cases = JSON.parse(readFileSync(fixturePath, "utf8")) as Array<{
    name: string; left: string; right: string; distance: number | null;
  }>;
  for (const positionCase of cases)
    expect(chessPositionDistance(positionCase.left, positionCase.right), positionCase.name)
      .toBe(positionCase.distance ?? undefined);
});

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

it("shared repertoire prefixes are traversed once without changing indexed line identity", () => {
  const startingFen = new Chess().fen();
  const sharedMoves = ["e2e4", "e7e5"];
  const lines = ["g1f3", "f1c4"].map((lastMove, index) => ({
    id: `line-${index}`, repertoireId: "white", repertoireName: "White",
    title: `Line ${index}`, side: "white" as const, startingFen,
    moves: [...sharedMoves, lastMove],
  }) as AnalysisLine);
  const expectedFirstBoard = new Chess(startingFen);
  for (const uci of lines[0].moves) expectedFirstBoard.move(uci);
  const expectedSecondBoard = new Chess(startingFen);
  for (const uci of lines[1].moves) expectedSecondBoard.move(uci);
  const moveSpy = vi.spyOn(Chess.prototype, "move");
  try {
    const positions = indexRepertoirePositions(lines);
    expect(positions.map(({ lineId, ply, nextUci }) => [lineId, ply, nextUci])).toEqual([
      ["line-0", 0, "e2e4"], ["line-0", 1, "e7e5"],
      ["line-0", 2, "g1f3"], ["line-0", 3, undefined],
      ["line-1", 0, "e2e4"], ["line-1", 1, "e7e5"],
      ["line-1", 2, "f1c4"], ["line-1", 3, undefined],
    ]);
    expect(positions[1].fen).toBe(positions[5].fen);
    expect(positions[2].fen).toBe(positions[6].fen);
    expect(positions[3].fen).toBe(expectedFirstBoard.fen());
    expect(positions[7].fen).toBe(expectedSecondBoard.fen());
    expect(moveSpy).toHaveBeenCalledTimes(4);
  } finally {
    moveSpy.mockRestore();
  }
});

it('pinned_and_irrelevant_en_passant_distinguish_exact_key_from_similarity', async () => {
  const { canonicalFenKey } = await import('../../app/utils/canonical-line');
  for (const withEp of [
    'k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
    'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1',
  ]) {
    const withoutEp = withEp.replace(/ (d6|e3) /, ' - ');
    expect(canonicalFenKey(withEp)).not.toBe(canonicalFenKey(withoutEp));
    expect(chessPositionDistance(withEp, withoutEp)).toBe(0);
  }
});

it.each([
  ['pinned', 'k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1', 'e1f1', 'e1d1'],
  ['irrelevant', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1', 'e7e5', 'c7c5'],
])('initial_opening_alternative_uses_same_legal_position_as_index: %s', async (_name, rawFen, expected, alternative) => {
  const { initialTrainingState } = await import('../../app/domain/attempt');
  const { queueCardsFromPayload } = await import('../../app/domain/adapters/practice-card-adapters');
  const { buildRepertoireAlternativeSnapshot, lookupRepertoireAlternative } = await import('../../app/lib/repertoire-alternative-index');
  const [card] = queueCardsFromPayload({ cards: [{ id:'card', queue_entry_id:1, start_fen:rawFen,
    moves:[expected], content_type:'opening', repertoire_name:'Rep', repertoire_source:'test',
    repertoire_id:'rep', trained_color: _name === 'pinned' ? 'white' : 'black' }] });
  const line = { id:'line', repertoireId:'rep', repertoireName:'Rep', title:'Alternative',
    side: _name === 'pinned' ? 'white' : 'black', startingFen:rawFen, moves:[alternative] } as AnalysisLine;
  const snapshot = buildRepertoireAlternativeSnapshot(1, indexRepertoirePositions([line]));
  expect(lookupRepertoireAlternative(snapshot, 1, card.repertoireId, initialTrainingState(card).fen, alternative)).toBe(true);
});


it('alternative_lookup_keeps_legal_ep_and_castling_distinct_and_invalid_unknown', async () => {
  const { buildRepertoireAlternativeSnapshot, lookupRepertoireAlternative } = await import('../../app/lib/repertoire-alternative-index');
  const legal = '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1';
  const line = { id:'line', repertoireId:'rep', repertoireName:'Rep', title:'line',
    side:'white', startingFen:legal, moves:['e1d1'] } as AnalysisLine;
  const snapshot = buildRepertoireAlternativeSnapshot(1, indexRepertoirePositions([line]));
  const rep = snapshot.positions[0].repertoireId as import('../../app/types').RepertoireId;
  expect(lookupRepertoireAlternative(snapshot, 1, rep, legal, 'e1d1')).toBe(true);
  expect(lookupRepertoireAlternative(snapshot, 1, rep, legal.replace(' d6 ', ' - '), 'e1d1')).toBe(false);
  expect(lookupRepertoireAlternative(snapshot, 1, rep, 'invalid', 'e1d1')).toBeUndefined();
  const castle = 'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1';
  const castled = buildRepertoireAlternativeSnapshot(2, indexRepertoirePositions([{ ...line, startingFen:castle, moves:['e1g1'] } as AnalysisLine]));
  expect(lookupRepertoireAlternative(castled, 2, rep, castle, 'e1g1')).toBe(true);
  expect(lookupRepertoireAlternative(castled, 2, rep, castle.replace(' KQkq ', ' - '), 'e1g1')).toBe(false);
});
