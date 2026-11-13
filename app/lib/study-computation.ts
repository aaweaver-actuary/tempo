import type { ValidationEffects, ValidationReporter } from "../domain/validation";
import type { AnalysisLine, PackagedPuzzle, PracticeCard } from "../types";
import type { repertoireLinesFromPayload } from "../domain/adapters/workspace-adapters";
import type { queueCardsFromPayload } from "../domain/adapters/practice-card-adapters";
import type { buildRepertoireAlternativeSnapshot } from "./repertoire-alternative-index";
import type { indexRepertoirePositions, IndexedPosition } from "./position-similarity";
import { computeEssentialStudyTask } from "./essential-study-computation";
import { computeOptionalStudyTask } from "./optional-study-computation";

export type PureStudyTask =
  | { kind: "workspace"; url: string; payload: unknown }
  | { kind: "transportLines"; payload: unknown }
  | { kind: "queue"; payload: unknown }
  | { kind: "deck"; records: unknown[]; deckId: string }
  | { kind: "lines"; lines: AnalysisLine[] }
  | { kind: "index"; lines: AnalysisLine[] }
  | { kind: "repertoireAlternatives"; generation: number; lines: AnalysisLine[] }
  | { kind: "repertoirePreparation"; generation: number; payload: unknown }
  | {
      kind: "similarity" | "matches";
      fen: string;
      positions: IndexedPosition[];
      limit?: number;
    };

export type StudyTask = PureStudyTask;

// This is the finite result contract of the existing computation branches.
// Workspace depends on its URL and stays unknown until its owner decodes it.
export type StudyTaskResults = {
  workspace: unknown;
  transportLines: ReturnType<typeof repertoireLinesFromPayload>;
  queue: ReturnType<typeof queueCardsFromPayload>;
  deck: Array<{ record: PackagedPuzzle; card: PracticeCard }>;
  lines: AnalysisLine[];
  index: ReturnType<typeof indexRepertoirePositions>;
  repertoireAlternatives: ReturnType<typeof buildRepertoireAlternativeSnapshot>;
  repertoirePreparation: ReturnType<typeof buildRepertoireAlternativeSnapshot>;
  matches: Array<IndexedPosition & { distance: number }>;
  similarity: Array<IndexedPosition & { distance: number }>;
};
export type StudyTaskResult<Task extends StudyTask> = StudyTaskResults[Task["kind"]];

// Compatibility facade for existing pure callers; worker entries import their own owner.
export function computeStudyTask<Task extends PureStudyTask>(task: Task, effects?: ValidationEffects | ValidationReporter): StudyTaskResult<Task>;
export function computeStudyTask(task: PureStudyTask, effects?: ValidationEffects | ValidationReporter) {
  switch (task.kind) {
    case "workspace": case "queue": case "deck": return computeEssentialStudyTask(task, effects);
    default: return computeOptionalStudyTask(task, effects);
  }
}
