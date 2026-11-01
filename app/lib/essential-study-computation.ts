import type { StudyTask } from "./study-computation";
import { packagedPuzzleSchema } from "../domain/schemas";
import { validRecords } from "./validated-data";
import { validateWorkspacePayload } from "../domain/adapters/workspace-adapters";
import { mapPackagedPuzzleToPracticeCard, queueCardsFromPayload } from "../domain/adapters/practice-card-adapters";

/** Essential queue, deck and workspace metadata computation. */
export function computeEssentialStudyTask(task: StudyTask) {
  switch (task.kind) {
    case "workspace":
      return validateWorkspacePayload(task.url, task.payload);
    case "queue":
      return queueCardsFromPayload(task.payload);
    case "deck":
      return validRecords(
        packagedPuzzleSchema,
        task.records.filter(
          (record) =>
            typeof record === "object" &&
            record !== null &&
            Reflect.get(record, "DeckId") === task.deckId,
        ),
        "packaged puzzle",
      )
        .sort((left, right) => left.DeckPosition - right.DeckPosition)
        .flatMap((record) => {
          const card = mapPackagedPuzzleToPracticeCard(record);
          return card ? [{ record, card }] : [];
        });
    default: throw new Error("Task does not belong to the essential study worker");
  }
}
