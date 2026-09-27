import { computeStudyTask, type StudyTask } from "./study-computation";
import { indexRepertoirePositions, type IndexedPosition } from "./position-similarity";

export function createStudyPositionStore() {
  const indexes = new Map<string, { revision: number; positions: IndexedPosition[] }>();
  return (task: StudyTask) => {
    switch (task.kind) {
      case "initializePositionIndex": {
        const current = indexes.get(task.repertoireId);
        if (current && current.revision > task.revision)
          throw new Error("A newer repertoire position index is already active.");
        if (current?.revision === task.revision)
          return { revision: current.revision, indexedPositions: current.positions.length };
        const positions = indexRepertoirePositions(task.lines);
        indexes.set(task.repertoireId, { revision: task.revision, positions });
        return { revision: task.revision, indexedPositions: positions.length };
      }
      case "findPositionMatches": {
        const current = indexes.get(task.repertoireId);
        if (current?.revision !== task.revision)
          throw new Error("Repertoire position index is unavailable. Reopen Builder to rebuild it.");
        return computeStudyTask({
          kind: "matches", fen: task.fen, positions: current.positions, limit: task.limit,
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
