import type { AnalysisLine, PackagedPuzzle } from "../types";
import { mapPackagedPuzzleToPracticeCard } from "../domain/adapters/practice-card-adapters";
import { canonicalizeLine } from "../utils/canonical-line";
import { chessPositionDistance, indexRepertoirePositions, type IndexedPosition } from "./position-similarity";

export type StudyTask =
  | { kind: "deck"; records: PackagedPuzzle[]; deckId: string }
  | { kind: "lines"; lines: AnalysisLine[] }
  | { kind: "index"; lines: AnalysisLine[] }
  | { kind: "similarity" | "matches"; fen: string; positions: IndexedPosition[] };

// Runs in the study worker in browsers, never during a React render.
export function computeStudyTask(task: StudyTask) {
  switch (task.kind) {
    case "deck":
      return task.records.filter(record => record.DeckId === task.deckId)
        .sort((left, right) => left.DeckPosition - right.DeckPosition)
        .flatMap(record => {
          const card = mapPackagedPuzzleToPracticeCard(record);
          return card ? [{ record, card }] : [];
        });
    case "lines":
      return [...new Map(task.lines.map(canonicalizeLine).map(line =>
        [`${line.repertoireId}:${line.startingFen.split(" ").slice(0, 4).join(" ")}:${line.moves.join(" ")}`, line],
      )).values()];
    case "index": return indexRepertoirePositions(task.lines.map(canonicalizeLine));
    case "matches":
    case "similarity":
      return task.positions.map(position => ({ ...position, distance: chessPositionDistance(task.fen, position.fen) }))
        .filter((position): position is IndexedPosition & { distance: number } => position.distance !== undefined && position.distance <= 2 && (task.kind === "matches" || position.nextUci !== undefined))
        .sort((left, right) => left.distance - right.distance || left.ply - right.ply)
        .filter((position, index, all) => all.findIndex(other => other.fen === position.fen && other.nextUci === position.nextUci) === index)
        .slice(0, 8);
  }
}
