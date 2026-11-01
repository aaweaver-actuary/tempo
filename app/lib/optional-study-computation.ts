import type { StudyTask } from "./study-computation";
import { validateWorkspacePayload, repertoireLinesFromPayload } from "../domain/adapters/workspace-adapters";
import { canonicalizeLine } from "../utils/canonical-line";
import { buildRepertoireAlternativeSnapshot } from "./repertoire-alternative-index";
import { indexRepertoirePositions, prepareChessPositionDistance, type IndexedPosition } from "./position-similarity";

/** Optional repertoire and position computation; no second copy of chess algorithms. */
export function computeOptionalStudyTask(task: StudyTask) {
  switch (task.kind) {
    case "workspace": return validateWorkspacePayload(task.url, task.payload);
    case "transportLines": return repertoireLinesFromPayload(task.payload);
    case "lines":
      return [
        ...new Map(
          task.lines
            .map(canonicalizeLine)
            .map((line) => [
              `${line.repertoireId}:${line.startingFen.split(" ").slice(0, 4).join(" ")}:${line.moves.join(" ")}`,
              line,
            ]),
        ).values(),
      ];
    case "index":
      return indexRepertoirePositions(task.lines.map(canonicalizeLine));
    case "repertoireAlternatives":
      return buildRepertoireAlternativeSnapshot(task.generation,
        indexRepertoirePositions(task.lines.map(canonicalizeLine)));
    case 'repertoirePreparation': {
      const decoded = typeof task.payload === 'string' ? JSON.parse(task.payload) : task.payload;
      const validated = validateWorkspacePayload('/api/repertoire/lines', decoded);
      const lines = repertoireLinesFromPayload(validated).map(canonicalizeLine);
      return buildRepertoireAlternativeSnapshot(task.generation, indexRepertoirePositions(lines));
    }
    case "matches":
    case "similarity": {
      const distanceToQuery = prepareChessPositionDistance(task.fen);
      const distanceByFen = new Map<string, number | undefined>();
      const seenMovesByFen = new Map<string, Set<string | undefined>>();
      const distanceForFen = (fen: string) => {
        if (distanceByFen.has(fen)) return distanceByFen.get(fen);
        const distance = distanceToQuery(fen);
        distanceByFen.set(fen, distance);
        return distance;
      };
      return task.positions
        .map((position) => ({
          ...position,
          distance: distanceForFen(position.fen),
        }))
        .filter(
          (position): position is IndexedPosition & { distance: number } =>
            position.distance !== undefined &&
            position.distance <= 2 &&
            (task.kind === "matches" || position.nextUci !== undefined),
        )
        .sort(
          (left, right) =>
            left.distance - right.distance || left.ply - right.ply,
        )
        .filter((position) => {
          const seenMoves = seenMovesByFen.get(position.fen);
          if (seenMoves?.has(position.nextUci)) return false;
          if (seenMoves) seenMoves.add(position.nextUci);
          else seenMovesByFen.set(position.fen, new Set([position.nextUci]));
          return true;
        })
        .slice(0, task.limit ?? 8);
    }
    default: throw new Error("Task does not belong to the optional study worker");
  }
}
