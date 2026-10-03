import { readFileSync } from "node:fs";
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { parseChessComPuzzlePgn } from "../../app/lib/tactic-capture-pgn";
import { solutionUciMoves } from "../../app/hooks/use-position-solution-editor";

const puzzlePgn = readFileSync("tests/fixtures/chesscom-puzzle-rush.pgn", "utf8");
const headerFen = "r1b2rk1/ppq2p1p/2np1Qp1/2b5/2B1Pp2/8/PPPP2PP/R1B1K1NR w KQ - 0 1";
const solverFen = "r1b2rk1/ppq2p1p/2np1Qp1/2b5/2B1Pp2/1P6/P1PP2PP/R1B1K1NR b KQ - 0 1";

it("Chess.com Puzzle Rush PGN advances the setup move and imports only the solver line", () => {
  expect(puzzlePgn).toContain(`[FEN "${headerFen}"]`);
  const imported = parseChessComPuzzlePgn(puzzlePgn);
  expect(imported).toEqual({ startingFen: solverFen, solutionSanMoves: ["Bd4", "Qxd4", "Nxd4"],
    sourceKind: "puzzle_rush", sourceRef: "NDA2OTUxNjU4NDc5NTM3NDc3MA==-b2b3",
    sourceUrl: "https://www.chess.com/puzzles/problem/3211014" });
  expect(solutionUciMoves(imported.startingFen, imported.solutionSanMoves)).toEqual(["c5d4", "f6d4", "c6d4"]);
});

it.each([
  ["empty", "", /Paste.*PGN/],
  ["whitespace", " \n ", /Paste.*PGN/],
  ["malformed", puzzlePgn.replace("1. b3 Bd4 2. Qxd4 Nxd4 *", "1. b3 {unfinished"), /PGN/],
  ["missing FEN", puzzlePgn.replace(/^\[FEN .*\]\n/m, ""), /FEN/],
  ["empty FEN", puzzlePgn.replace(headerFen, ""), /FEN/],
  ["invalid FEN", puzzlePgn.replace(headerFen, "invalid FEN"), /FEN/],
  ["no mainline", puzzlePgn.replace("1. b3 Bd4 2. Qxd4 Nxd4 *", "*"), /setup move.*solution/],
  ["setup only", puzzlePgn.replace("1. b3 Bd4 2. Qxd4 Nxd4 *", "1. b3 *"), /setup move.*solution/],
  ["illegal setup", puzzlePgn.replace("1. b3", "1. b5"), /PGN.*move/],
  ["illegal continuation", puzzlePgn.replace("Qxd4 Nxd4", "Qxd4 Nc6"), /PGN.*move/],
  ["null move", puzzlePgn.replace("1. b3 Bd4 2. Qxd4 Nxd4 *", "1. b3 -- *"), /PGN.*move/],
])("Chess.com puzzle import rejects %s input", (_description, text, message) => {
  expect(() => parseChessComPuzzlePgn(text)).toThrow(message);
});

it("arbitrary game PGN is not silently treated as Chess.com Puzzle Rush", () => {
  const ordinaryGame = puzzlePgn.replace(/^\[(PuzzleID|Link) .*\]\n/gm, "");
  expect(() => parseChessComPuzzlePgn(ordinaryGame)).toThrow(/PuzzleID.*Chess.com.*puzzles/);
});

it.each([
  "https://chess.com/puzzles/problem/42", "http://www.chess.com/puzzles/problem/42",
])("Chess.com puzzle import accepts a puzzle Link without PuzzleID: %s", sourceUrl => {
  const withoutId = puzzlePgn.replace(/^\[PuzzleID .*\]\n/m, "").replace("https://www.chess.com/puzzles/problem/3211014", sourceUrl);
  expect(parseChessComPuzzlePgn(withoutId)).toMatchObject({ sourceRef: null, sourceUrl });
});

it.each(["https://chess.com/games/42", "https://chess.com.example.org/puzzles/problem/42", "javascript://chess.com/puzzles/42", "not-a-url"])(
  "non-puzzle Link cannot identify a Chess.com puzzle: %s", sourceUrl => {
    const withoutId = puzzlePgn.replace(/^\[PuzzleID .*\]\n/m, "").replace("https://www.chess.com/puzzles/problem/3211014", sourceUrl);
    expect(() => parseChessComPuzzlePgn(withoutId)).toThrow(/PuzzleID/);
  });

it("Chess.com PuzzleID identifies a puzzle without Link and preserves an invalid Link for existing save validation", () => {
  expect(parseChessComPuzzlePgn(puzzlePgn.replace(/^\[Link .*\]\n/m, ""))).toMatchObject({ sourceUrl: null });
  expect(parseChessComPuzzlePgn(puzzlePgn.replace("https://www.chess.com/puzzles/problem/3211014", "not-a-url"))).toMatchObject({ sourceUrl: "not-a-url" });
});

it("Chess.com puzzle import retains an explicit standard-position FEN header", () => {
  const text = `[FEN "${new Chess().fen()}"]\n[PuzzleID "standard"]\n\n1. e4 e5 *`;
  const expected = new Chess(); expected.move("e4");
  expect(parseChessComPuzzlePgn(text)).toMatchObject({ startingFen: expected.fen(), solutionSanMoves: ["e5"] });
});

it("Chess.com puzzle import derives a White learner after a Black setup move", () => {
  const blackFen = new Chess().fen().replace(" w ", " b ");
  const text = `[FEN "${blackFen}"]\n[PuzzleID "black-setup"]\n\n1... e5 2. Nf3 Nc6 *`;
  const expected = new Chess(blackFen); expected.move("e5");
  expect(parseChessComPuzzlePgn(text)).toMatchObject({ startingFen: expected.fen(), solutionSanMoves: ["Nf3", "Nc6"] });
  expect(expected.turn()).toBe("w");
});

it("Chess.com puzzle import uses only the mainline and ignores comments and variations", () => {
  const text = puzzlePgn.replace("1. b3 Bd4", "1. b3 {setup} (1. d3) Bd4");
  expect(parseChessComPuzzlePgn(text)).toEqual(parseChessComPuzzlePgn(puzzlePgn));
});
