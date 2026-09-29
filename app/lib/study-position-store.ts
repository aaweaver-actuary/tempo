import { Chess } from "chess.js";
import { computeStudyTask, type StudyTask } from "./study-computation";
import { indexRepertoirePositions, type IndexedPosition } from "./position-similarity";
import { prepareChessPositionDistance } from "./position-similarity";
import type { ComparisonPosition } from "./comparison";

function compatibilitySignature(canonicalFen: string): string {
  const [placement, turn, castling, enPassant] = canonicalFen.split(" ");
  const material = placement.replace(/[1-8/]/g, "").split("").sort().join("");
  return `${turn}|${castling}|${enPassant}|${material}`;
}

export function createStudyPositionStore() {
  let comparisonIndex: {
    revision: number;
    byCompatibility: Map<string, Array<ComparisonPosition & { repertoireIds: string[] }>>;
    sanByCardId: Map<string, string[]>;
  } | undefined;
  const indexes = new Map<string, {
    revision: number; indexedPositions: number;
    candidatesByCompatibility: Map<string, IndexedPosition[]>;
  }>();
  return (task: StudyTask) => {
    switch (task.kind) {
      case "initializeComparisonIndex": {
        if (comparisonIndex && comparisonIndex.revision > task.revision)
          return { indexedCards: 0 };
        if (comparisonIndex?.revision === task.revision) return { indexedCards: task.cards.length };
        const byCompatibility = new Map<string, Array<ComparisonPosition & { repertoireIds: string[] }>>();
        const sanByCardId = new Map<string, string[]>();
        for (const card of task.cards) {
          try {
            const board = new Chess(card.start_fen);
            const cardPositions: Array<ComparisonPosition & { repertoireIds: string[] }> = [];
            const sanMoves: string[] = [];
            for (let ply = 0; ply <= card.moves.length; ply += 1) {
              const fen = board.fen();
              cardPositions.push({
                cardId: card.id, fen, ply, nextUci: card.moves[ply], distance: 0,
                repertoireIds: card.repertoires.map((repertoire) => repertoire.id),
              });
              if (ply < card.moves.length) sanMoves.push(board.move({
                from: card.moves[ply].slice(0, 2) as import("chess.js").Square,
                to: card.moves[ply].slice(2, 4) as import("chess.js").Square,
                promotion: card.moves[ply][4] || undefined,
              }).san);
            }
            sanByCardId.set(card.id, sanMoves);
            for (const position of cardPositions) {
              const signature = compatibilitySignature(position.fen);
              const positions = byCompatibility.get(signature) ?? [];
              positions.push(position);
              byCompatibility.set(signature, positions);
            }
          } catch {
            // An invalid saved route must not appear as a partial comparison.
          }
        }
        comparisonIndex = { revision: task.revision, byCompatibility, sanByCardId };
        return { indexedCards: task.cards.length };
      }
      case "findComparisonMatches": {
        if (comparisonIndex?.revision !== task.revision)
          throw new Error("Comparison index is unavailable. Reopen Compare to reload cards.");
        let signature: string;
        try { signature = compatibilitySignature(new Chess(task.fen).fen()); }
        catch { return []; }
        const distanceToQuery = prepareChessPositionDistance(task.fen);
        const distanceByFen = new Map<string, number | undefined>();
        return (comparisonIndex.byCompatibility.get(signature) ?? []).flatMap((position) => {
          if (task.repertoireId && !position.repertoireIds.includes(task.repertoireId)) return [];
          if (!distanceByFen.has(position.fen))
            distanceByFen.set(position.fen, distanceToQuery(position.fen));
          const distance = distanceByFen.get(position.fen);
          return distance === undefined || distance > 2 ? [] : [{
            cardId: position.cardId, fen: position.fen, ply: position.ply,
            nextUci: position.nextUci, distance,
            routeSan: comparisonIndex?.sanByCardId.get(position.cardId)?.slice(0, position.ply),
          } satisfies ComparisonPosition];
        });
      }
      case "releaseComparisonIndex": {
        if (comparisonIndex?.revision !== task.revision) return false;
        comparisonIndex = undefined;
        return true;
      }
      case "initializePositionIndex": {
        const current = indexes.get(task.repertoireId);
        if (current && current.revision > task.revision)
          throw new Error("A newer repertoire position index is already active.");
        if (current?.revision === task.revision)
          return { revision: current.revision, indexedPositions: current.indexedPositions };
        const positions = indexRepertoirePositions(task.lines);
        const candidatesByCompatibility = new Map<string, IndexedPosition[]>();
        const signatureByFen = new Map<string, string>();
        for (const position of positions) {
          let signature = signatureByFen.get(position.fen);
          if (!signature) {
            signature = compatibilitySignature(position.fen);
            signatureByFen.set(position.fen, signature);
          }
          const candidates = candidatesByCompatibility.get(signature);
          if (candidates) candidates.push(position);
          else candidatesByCompatibility.set(signature, [position]);
        }
        indexes.set(task.repertoireId, {
          revision: task.revision, indexedPositions: positions.length, candidatesByCompatibility,
        });
        return { revision: task.revision, indexedPositions: positions.length };
      }
      case "findPositionMatches": {
        const current = indexes.get(task.repertoireId);
        if (current?.revision !== task.revision)
          throw new Error("Repertoire position index is unavailable. Reopen Builder to rebuild it.");
        let querySignature: string;
        try {
          querySignature = compatibilitySignature(new Chess(task.fen).fen());
        } catch {
          return [];
        }
        return computeStudyTask({
          kind: "matches", fen: task.fen,
          positions: current.candidatesByCompatibility.get(querySignature) ?? [], limit: task.limit,
        });
      }
      case "releasePositionIndex": {
        if (indexes.get(task.repertoireId)?.revision !== task.revision) return false;
        indexes.delete(task.repertoireId);
        return true;
      }
      default:
        return computeStudyTask(task);
    }
  };
}
