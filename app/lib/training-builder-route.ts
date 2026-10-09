import { Chess } from "chess.js";
import type { BuilderHistoryEntry, BuilderSession, CanonicalLine, PracticeCard } from "../types";
import { asFenString, asSanMove, asUciMove } from "../types";
import { canonicalFenKey } from "../utils/canonical-line";
import { trainedColor } from "../utils/cards";

export type TrainingPositionContext = { fen: string; cursor: number };
export type TrainingRouteCandidate = {
  startingFen: string;
  prefix: BuilderHistoryEntry[];
  title: string;
};

export function trainingBuilderSession(card: PracticeCard, displayed: TrainingPositionContext, draft: boolean): BuilderSession {
  const board = new Chess(card.startingFen);
  const history = card.moves.map(san => {
    const move = board.move(san);
    return { san: asSanMove(move.san), uci: asUciMove(`${move.from}${move.to}${move.promotion ?? ""}`), fen: asFenString(board.fen()) };
  });
  const cursor = displayed.cursor;
  if (!Number.isInteger(cursor) || cursor < 0 || cursor > history.length ||
      canonicalFenKey(cursor ? history[cursor - 1].fen : card.startingFen) !== canonicalFenKey(displayed.fen))
    throw new Error("The viewed position does not match this card's saved route. Return to the card position or repair its moves.");
  const orientation = trainedColor(card);
  return {
    version: 1, orientation, activeRepertoireId: card.repertoireId,
    activeRepertoireByColor: card.repertoireId ? { [orientation]: card.repertoireId } : {},
    startingFen: card.startingFen, history, cursor, branchStart: draft ? cursor : null,
    trainingRouteToResolve: card.kind === "opening" && card.repertoireId ? {
      repertoireId: card.repertoireId, cardId: card.backendId ?? card.id, cardRevision: card.revision ?? 1,
    } : undefined,
  };
}

// Exact route recovery runs once in the study worker, outside rendering and database reads.
export function resolveTrainingRoute(lines: CanonicalLine[], repertoireId: string, startingFen: string, moves: string[]): TrainingRouteCandidate[] {
  const candidatesByRoute = new Map<string, TrainingRouteCandidate>();
  const cardStartKey = canonicalFenKey(new Chess(startingFen).fen());
  for (const line of lines) {
    if (line.repertoireId !== repertoireId) continue;
    const board = new Chess(line.startingFen);
    const prefix: BuilderHistoryEntry[] = [];
    for (let ply = 0; ply <= line.moves.length; ply += 1) {
      if (canonicalFenKey(board.fen()) === cardStartKey && moves.every((move, index) => line.moves[ply + index] === move)) {
        const routeKey = `${canonicalFenKey(line.startingFen)}:${prefix.map(move => move.uci).join(" ")}`;
        if (!candidatesByRoute.has(routeKey)) candidatesByRoute.set(routeKey, {
          startingFen: line.startingFen, prefix: [...prefix], title: line.title || line.repertoireName,
        });
      }
      if (ply < line.moves.length) {
        const uci = line.moves[ply];
        const move = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
        prefix.push({ san: asSanMove(move.san), uci, fen: asFenString(board.fen()) });
      }
    }
  }
  return [...candidatesByRoute.values()];
}

export function prependTrainingRoute(candidate: TrainingRouteCandidate, cardHistory: BuilderHistoryEntry[]): BuilderHistoryEntry[] {
  const board = new Chess(candidate.prefix.at(-1)?.fen ?? candidate.startingFen);
  return [...candidate.prefix, ...cardHistory.map(entry => {
    const move = board.move({ from: entry.uci.slice(0, 2), to: entry.uci.slice(2, 4), promotion: entry.uci[4] });
    return { san: asSanMove(move.san), uci: entry.uci, fen: asFenString(board.fen()) };
  })];
}
