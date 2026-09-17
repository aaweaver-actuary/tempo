import { describe, expect, it } from "vitest";
import { fitBoardSurface } from "../../app/hooks/use-board-viewport";

describe("board coordinate geometry", () => {
  it("frame padding remains outside equal square board dimensions at desktop, narrow, and high-DPI sizes", () => {
    for (const bounds of [
      { width: 700, height: 620 },
      { width: 354, height: 500 },
      { width: 353.5, height: 353.5 },
      { width: 800, height: 300 },
    ]) {
      const surface = fitBoardSurface(bounds, 9);
      expect(surface % 8).toBe(0);
      expect(surface + 18).toBeLessThanOrEqual(
        Math.min(bounds.width, bounds.height),
      );
      expect(surface).toBeGreaterThan(0);
    }
  });
});
