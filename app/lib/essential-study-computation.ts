import type { StudyTask } from "./study-computation";
import { packagedPuzzleSchema } from "../domain/schemas";
import { validRecords, type ValidationReporter, type ValidationEffects } from "../domain/validation";
import { validateWorkspacePayload } from "../domain/adapters/workspace-adapters";
import { mapPackagedPuzzleToPracticeCard, queueCardsFromPayload } from "../domain/adapters/practice-card-adapters";

/** Essential queue, deck and workspace metadata computation. */
export function computeEssentialStudyTask(task: StudyTask, reporter?: ValidationReporter | ValidationEffects) {
  switch (task.kind) {
    case "workspace":
      return validateWorkspacePayload(task.url, task.payload, undefined, reporter);
    case "queue":
      return queueCardsFromPayload(task.payload, reporter);
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
        typeof reporter === "function" ? { reportError: reporter } : reporter,
      )
        .sort((left, right) => left.DeckPosition - right.DeckPosition)
        .flatMap((record) => {
          const card = mapPackagedPuzzleToPracticeCard(record, reporter);
          return card ? [{ record, card }] : [];
        });
    default: throw new Error("Task does not belong to the essential study worker");
  }
}
