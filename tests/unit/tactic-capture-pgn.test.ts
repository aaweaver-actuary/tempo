import { readFileSync } from "node:fs";
import { Chess } from "chess.js";
import { expect, it } from "vitest";
import { parseChessComPuzzlePgn } from "../../app/lib/tactic-capture-pgn";
import { fetchLichessPuzzle, parseLichessPuzzle } from "../../app/lib/tactic-capture-lichess";
import { vi } from "vitest";
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

const lichess = JSON.parse(readFileSync("tests/fixtures/lichess-puzzle-api.json", "utf8"));
it("Lichess official API fixture starts after setup and retains the complete Black solution", () => {
  const imported = parseLichessPuzzle(lichess, "PSjmf");
  expect(imported).toMatchObject({ startingFen: lichess.puzzle.fen, sourceKind: "other", sourceRef: "PSjmf",
    sourceUrl: "https://lichess.org/training/PSjmf" });
  expect(solutionUciMoves(imported.startingFen, imported.solutionSanMoves)).toEqual(lichess.puzzle.solution);
  expect(imported.solutionSanMoves).toEqual(["Kg7", "Qe5", "Qxe5+"]);
});

it("Lichess fallback replays all setup plies without dropping the first solution move for either solver color", () => {
  const withoutFen = structuredClone(lichess); delete withoutFen.puzzle.fen;
  const black = parseLichessPuzzle(withoutFen, "PSjmf");
  expect(black.startingFen.split(" ").slice(0, 4)).toEqual(lichess.puzzle.fen.split(" ").slice(0, 4));
  expect(solutionUciMoves(black.startingFen, black.solutionSanMoves)).toEqual(lichess.puzzle.solution);
  const white = parseLichessPuzzle({ game: { pgn: "e4 e5" }, puzzle: {
    id: "White", initialPly: 1, solution: ["g1f3", "b8c6", "f1b5"],
  } }, "White");
  expect(white.startingFen).toBe("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2");
  expect(white.solutionSanMoves).toEqual(["Nf3", "Nc6", "Bb5"]);
});

it.each([
  { puzzle: { ...lichess.puzzle, id: "wrong" } },
  { puzzle: { ...lichess.puzzle, initialPly: 51 } },
  { puzzle: { ...lichess.puzzle, fen: new Chess().fen() } },
  { puzzle: { ...lichess.puzzle, solution: [] } },
  { puzzle: { ...lichess.puzzle, solution: ["g8g1"] } },
  { game: { pgn: "e4 invalid" } },
])("Lichess invalid identity, setup or full solution fails before capture: %j", override => {
  expect(() => parseLichessPuzzle({ ...lichess, ...override }, "PSjmf")).toThrow(/invalid/);
});

it("Lichess acquisition uses only the explicit official ID endpoint and preserves provider failure", async () => {
  const fetcher = vi.fn(async () => Response.json(lichess));
  vi.stubGlobal("fetch", fetcher);
  try {
    await fetchLichessPuzzle(" PSjmf ", new AbortController().signal);
    expect(fetcher).toHaveBeenCalledWith("https://lichess.org/api/puzzle/PSjmf", expect.objectContaining({ credentials: "omit" }));
    fetcher.mockResolvedValueOnce(new Response("unavailable", { status: 503 }));
    await expect(fetchLichessPuzzle("PSjmf", new AbortController().signal)).rejects.toThrow(/503/);
    await expect(fetchLichessPuzzle("../x", new AbortController().signal)).rejects.toThrow(/ID/);
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally { vi.unstubAllGlobals(); }
});
