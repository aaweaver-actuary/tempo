import type TrainingView from "../../../views/training_view";

/** Exact existing practice-view signature, including its owner-supplied callbacks. */
export type TrainingViewProps = Parameters<typeof TrainingView>[0];
export type StudyPracticeComponent = typeof TrainingView;

/** The composition root chooses when to load the retained private practice view. */
export async function loadTrainingView(): Promise<StudyPracticeComponent> {
  return (await import("../../../views/training_view")).default;
}
