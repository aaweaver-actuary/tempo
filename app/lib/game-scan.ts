import { Chess } from "chess.js";
import type { EngineMove } from "./analysis-engines";

export type MoveEvaluation = { ply: number; before_cp: number; after_cp: number; opponent_created_chance: boolean };

export type DurableMoveEvaluation = MoveEvaluation & {
  depth: number;
  position_fen: string;
  best_move_uci?: string;
  principal_variation: string[];
  candidate_lines: Array<{
    uci: string;
    cp?: number;
    mate?: number;
    score?: string;
    pv: string[];
  }>;
  mate_before?: number;
  mate_after?: number;
  mover_color: "white" | "black";
  is_player_move: boolean;
  actual_move_uci: string;
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
  onProgress?: (phase: string, completed: number, total: number) => void,
): Promise<DurableMoveEvaluation[]> {
  const board = new Chess(startFen);
  const positionFens = [board.fen()];
  const positionTurns: Array<"w" | "b"> = [board.turn()];
  const positionCheckmates = [board.isCheckmate()];
  for (const move of moves) {
    board.move(move);
    positionFens.push(board.fen());
    positionTurns.push(board.turn());
    positionCheckmates.push(board.isCheckmate());
  }
  const analysisCache = new Map<string, EngineMove[]>();
  const analyzePosition = async (positionIndex: number, depth: number) => {
    const cacheKey = `${depth}:${positionFens[positionIndex]}`;
    if (!analysisCache.has(cacheKey)) {
      analysisCache.set(
        cacheKey,
        (await analyze(positionFens[positionIndex], depth)).slice(0, 5),
      );
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
    }
    return analysisCache.get(cacheKey) ?? [];
  };
  const shallowLines: EngineMove[][] = [];
  for (let positionIndex = 0; positionIndex < positionFens.length; positionIndex++) {
    if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    shallowLines.push(await analyzePosition(positionIndex, 8));
    onProgress?.("Scanning positions", positionIndex + 1, positionFens.length + moves.length);
  }
  const evaluations: DurableMoveEvaluation[] = [];
  for (let ply = 0; ply < moves.length; ply++) {
    const beforeTurn = positionTurns[ply];
    const afterTurn = positionTurns[ply + 1];
    const moverColor = beforeTurn === "w" ? "white" : "black";
    const moverSign = moverColor === "white" ? 1 : -1;
    const shallowBefore = shallowLines[ply][0];
    const shallowAfter = shallowLines[ply + 1][0];
    const shallowBeforeValue = whiteEvaluation(
      shallowBefore,
      beforeTurn,
      positionCheckmates[ply],
    );
    const shallowAfterValue = whiteEvaluation(
      shallowAfter,
      afterTurn,
      positionCheckmates[ply + 1],
    );
    const shallowLoss = (shallowBeforeValue - shallowAfterValue) * moverSign;
    const shouldConfirm = shallowLoss >= 75 || ply === repertoireDeviationPly || shallowBefore?.mate !== undefined || shallowAfter?.mate !== undefined;
    const confirmedBeforeLines = shouldConfirm
      ? await analyzePosition(ply, 14)
      : shallowLines[ply];
    const confirmedAfterLines = shouldConfirm
      ? await analyzePosition(ply + 1, 14)
      : shallowLines[ply + 1];
    const confirmedBefore = confirmedBeforeLines[0];
    const confirmedAfter = confirmedAfterLines[0];
    evaluations.push({
      ply,
      before_cp: whiteEvaluation(confirmedBefore, beforeTurn, positionCheckmates[ply]),
      after_cp: whiteEvaluation(confirmedAfter, afterTurn, positionCheckmates[ply + 1]),
      opponent_created_chance: false,
      depth: shouldConfirm ? 14 : 8,
      position_fen: positionFens[ply],
      best_move_uci: confirmedBefore?.uci,
      principal_variation: confirmedBefore?.pv ?? [],
      candidate_lines: confirmedBeforeLines.map((line) => ({
        uci: line.uci,
        cp: line.cp,
        mate: line.mate,
        score: line.score,
        pv: line.pv ?? [line.uci],
      })),
      mate_before: confirmedBefore?.mate,
      mate_after: confirmedAfter?.mate,
      mover_color: moverColor,
      is_player_move: moverColor === color,
      actual_move_uci: moves[ply],
    });
    onProgress?.("Reviewing moves", positionFens.length + ply + 1, positionFens.length + moves.length);
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
