import { describe, expect, it } from "vitest";
import { practicePriorityCopy } from "../../app/lib/practice-priority";

describe("historical exposure priority copy", () => {
  it("identifies encountered-line practice without claiming historical forgetting", () => {
    expect(practicePriorityCopy("historical_exposure")).toEqual({
      label: "Encountered line practice",
      explanation: "Practice requested for a line encountered in a game; this is not a forgotten-recall finding.",
    });
  });
  it("preserves other priority reasons and does not invent a label", () => {
    for (const reason of ["real_game_miss", "legacy_real_game_miss", "other-priority"]) {
      expect(practicePriorityCopy(reason)).toEqual({ label: reason });
    }
    expect(practicePriorityCopy(null)).toBeNull();
    expect(practicePriorityCopy(undefined)).toBeNull();
  });
});
