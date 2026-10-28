import { Chess } from "chess.js";
import { z } from "zod";
import { asFenString, asSanMove } from "../types";

const puzzleResponse = z.object({
  game: z.object({ pgn: z.string().min(1).max(100_000) }),
  puzzle: z.object({
    id: z.string(), initialPly: z.number().int().nonnegative(),
    fen: z.string().optional(),
    solution: z.array(z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/)).min(1).max(512),
  }),
});

export function isLichessPuzzleId(input: string): boolean {
  return /^[a-zA-Z0-9]{5}$/.test(input.trim());
}

export function parseLichessPuzzle(payload: unknown, requestedId: string) {
  const parsed = puzzleResponse.safeParse(payload);
  if (!parsed.success || parsed.data.puzzle.id !== requestedId)
    throw new Error("Lichess returned an invalid or mismatched puzzle. No capture was saved.");
  const { game, puzzle } = parsed.data;
  try {
    const replay = new Chess();
    replay.loadPgn(game.pgn, { strict: false });
    const history = replay.history();
    // Official game.pgn ends AFTER the setup ply (initialPly is zero-based).
    if (history.length !== puzzle.initialPly + 1 || history.includes("--")) throw new Error("setup");
    const startingFen = puzzle.fen ? new Chess(puzzle.fen).fen() : replay.fen();
    // The optional API FEN normalizes clocks; compare the position, not clocks.
    if (startingFen.split(" ").slice(0, 4).join(" ") !== replay.fen().split(" ").slice(0, 4).join(" "))
      throw new Error("position");
    const board = new Chess(startingFen);
    // API solution already excludes setup. Keep EVERY solution move (unlike CSV).
    const solutionSanMoves = puzzle.solution.map(uci => asSanMove(board.move({
      from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4],
    }).san));
    return { startingFen: asFenString(startingFen), solutionSanMoves,
      sourceKind: "other" as const, sourceRef: puzzle.id, sourceUrl: `https://lichess.org/training/${puzzle.id}` };
  } catch {
    throw new Error("Lichess returned an invalid puzzle position or solution. No capture was saved.");
  }
}

export async function fetchLichessPuzzle(input: string, signal: AbortSignal) {
  const id = input.trim();
  if (!isLichessPuzzleId(id)) throw new Error("Enter a five-character Lichess puzzle ID.");
  const response = await fetch(`https://lichess.org/api/puzzle/${id}`, {
    headers: { Accept: "application/json" }, credentials: "omit",
    signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
  });
  if (!response.ok) throw new Error(`Could not load the Lichess puzzle (${response.status}). Your input is kept; try again.`);
  return parseLichessPuzzle(await response.json(), id);
}
