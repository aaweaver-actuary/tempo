import { expect, it } from "vitest";
import { formatConversionRate, tacticalQueueBoardOrientation } from "../../app/lib/tactical-opportunities";

it("tactical statistics render an explicit zero-denominator rate", () => {
  expect(formatConversionRate(0, 0)).toBe("—");
  expect(formatConversionRate(7, 12)).toBe("58.3%");
});

it("tactical queue uses the player's board orientation", () => {
  expect(tacticalQueueBoardOrientation("white")).toBe("white");
  expect(tacticalQueueBoardOrientation("black")).toBe("black");
});
