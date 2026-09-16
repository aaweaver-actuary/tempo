import { Chess } from "chess.js";
import type { EngineMove } from "./analysis-engines";

export type MoveEvaluation = { ply: number; before_cp: number; after_cp: number; opponent_created_chance: boolean };

export async function scanGame(startFen: string, moves: string[], color: "white" | "black", analyze: (fen: string) => Promise<EngineMove[]>, signal?: AbortSignal): Promise<MoveEvaluation[]> {
  const board = new Chess(startFen);
  const values: number[] = [];
  const turns: string[] = [];
  for (let ply = 0; ply <= moves.length; ply++) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    turns.push(board.turn());
    const line = (await analyze(board.fen()))[0];
    if (!line) values.push(board.isCheckmate() ? (board.turn() === "w" ? -10_000 : 10_000) : 0);
    else values.push((line.mate !== undefined ? Math.sign(line.mate) * 10_000 : line.cp ?? 0) * (board.turn() === "w" ? 1 : -1));
    if (ply < moves.length) board.move(moves[ply]);
  }
  const sign = color === "white" ? 1 : -1;
  return moves.map((_, ply) => ({ ply, before_cp: values[ply], after_cp: values[ply + 1], opponent_created_chance: ply > 0 && turns[ply] === color[0] && (values[ply] - values[ply - 1]) * sign >= 100 }));
}
