import { Chess } from "chess.js";
import type { EngineMove } from "./analysis-engines";

export type MoveEvaluation = { ply: number; before_cp: number; after_cp: number; opponent_created_chance: boolean };

export type DurableMoveEvaluation = MoveEvaluation & {
  depth: number;
  best_move_uci?: string;
  principal_variation: string[];
  mate_before?: number;
  mate_after?: number;
};

function whiteEvaluation(line: EngineMove | undefined, turn: "w" | "b", checkmate: boolean) {
  if (!line) return checkmate ? (turn === "w" ? -10_000 : 10_000) : 0;
  const sideToMoveScore = line.mate !== undefined ? Math.sign(line.mate) * 10_000 : line.cp ?? 0;
  return sideToMoveScore * (turn === "w" ? 1 : -1);
}

export async function scanGameTwoPass(
  startFen: string,
  moves: string[],
  color: "white" | "black",
  analyze: (fen: string, depth: number) => Promise<EngineMove[]>,
  repertoireDeviationPly?: number | null,
  signal?: AbortSignal,
): Promise<DurableMoveEvaluation[]> {
  const board = new Chess(startFen);
  const playerTurn = color === "white" ? "w" : "b";
  const playerSign = color === "white" ? 1 : -1;
  const evaluations: DurableMoveEvaluation[] = [];
  for (let ply = 0; ply < moves.length; ply++) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    const playerMoved = board.turn() === playerTurn;
    if (!playerMoved) {
      board.move(moves[ply]);
      continue;
    }
    const beforeFen = board.fen();
    const beforeTurn = board.turn();
    const shallowBefore = (await analyze(beforeFen, 8))[0];
    const shallowBeforeValue = whiteEvaluation(shallowBefore, beforeTurn, board.isCheckmate());
    board.move(moves[ply]);
    const afterFen = board.fen();
    const afterTurn = board.turn();
    const shallowAfter = (await analyze(afterFen, 8))[0];
    const shallowAfterValue = whiteEvaluation(shallowAfter, afterTurn, board.isCheckmate());
    const shallowLoss = (shallowBeforeValue - shallowAfterValue) * playerSign;
    const shouldConfirm = shallowLoss >= 75 || ply === repertoireDeviationPly || shallowBefore?.mate !== undefined || shallowAfter?.mate !== undefined;
    const confirmedBefore = shouldConfirm ? (await analyze(beforeFen, 14))[0] : shallowBefore;
    const confirmedAfter = shouldConfirm ? (await analyze(afterFen, 14))[0] : shallowAfter;
    evaluations.push({
      ply,
      before_cp: whiteEvaluation(confirmedBefore, beforeTurn, false),
      after_cp: whiteEvaluation(confirmedAfter, afterTurn, board.isCheckmate()),
      opponent_created_chance: false,
      depth: shouldConfirm ? 14 : 8,
      best_move_uci: confirmedBefore?.uci,
      principal_variation: confirmedBefore?.pv ?? [],
      mate_before: confirmedBefore?.mate,
      mate_after: confirmedAfter?.mate,
    });
    await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
  }
  return evaluations;
}

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
