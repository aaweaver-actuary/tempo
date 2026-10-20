/** Display authority is distinct from a finding that the learner forgot a move. */
export function practicePriorityCopy(reason: string | null | undefined): {
  label: string;
  explanation?: string;
} | null {
  if (!reason) return null;
  if (reason === "historical_exposure") {
    return {
      label: "Encountered line practice",
      explanation: "Practice requested for a line encountered in a game; this is not a forgotten-recall finding.",
    };
  }
  return { label: reason };
}
