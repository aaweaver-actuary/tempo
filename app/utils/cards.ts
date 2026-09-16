import { Chess } from "chess.js";
import {
  AnalysisLine,
  BackendQueueCard,
  CardNameSource,
  PackagedPuzzle,
  PieceColor,
  PracticeCard,
} from "../types";
import { movesToSanFormat } from "./chess";
import {
  mapPackagedPuzzleToPracticeCard,
  mapQueueCardToPracticeCard,
} from "../domain/adapters/practice-card-adapters";

// Converts a backend queue card into a practice card for the training interface.
export function practiceCardFromQueue(card: BackendQueueCard): PracticeCard {
  return mapQueueCardToPracticeCard(card);
}

// Returns the color that the user is expected to play for a given practice card.
export function trainedColor(card: PracticeCard): PieceColor {
  if (card.orientation) return card.orientation;
  return card.kind === "opening"
    ? "white"
    : new Chess(card.startingFen).turn() === "b"
      ? "black"
      : "white";
}

// Returns a human-readable name for a given analysis line, either using its title or the SAN moves.
export function getHumanReadableLineName(
  line: Pick<AnalysisLine, CardNameSource>,
) {
  if (
    line.title &&
    !/^(analysis branch|line|variation)$/i.test(line.title.trim())
  )
    return line.title;
  try {
    return movesToSanFormat(line.startingFen, line.moves).join(" ");
  } catch {
    return line.moves.join(" ");
  }
}

export function convertPackagedPuzzleRecordIntoPracticeCard(
  record: PackagedPuzzle,
): PracticeCard | null {
  return mapPackagedPuzzleToPracticeCard(record);
}
