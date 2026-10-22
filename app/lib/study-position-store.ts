import { Chess } from "chess.js";
import { computeStudyTask, type StudyTask } from "./study-computation";
import { indexRepertoirePositions, type IndexedPosition } from "./position-similarity";

function compatibilitySignature(canonicalFen: string): string {
  const [placement, turn, castling, enPassant] = canonicalFen.split(" ");
  const material = placement.replace(/[1-8/]/g, "").split("").sort().join("");
  return `${turn}|${castling}|${enPassant}|${material}`;
}

export function createStudyPositionStore() {
  const indexes = new Map<string, {
    revision: number; indexedPositions: number;
    candidatesByCompatibility: Map<string, IndexedPosition[]>;
  }>();
  return (task: StudyTask) => {
    switch (task.kind) {
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
