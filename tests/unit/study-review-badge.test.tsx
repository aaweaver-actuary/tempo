import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import StudyReviewBadge from "../../app/components/StudyReviewBadge";

it("labels a card's prior study review count", () => {
  const { rerender } = render(<StudyReviewBadge count={0} />);
  expect(screen.getByText("First time")).toBeTruthy();

  rerender(<StudyReviewBadge count={1} />);
  expect(screen.getByText("Seen before 1 time")).toBeTruthy();

  rerender(<StudyReviewBadge count={4} />);
  expect(screen.getByText("Seen before 4 times")).toBeTruthy();
});

it("omits the badge when a legacy queue response has no count", () => {
  const { container } = render(<StudyReviewBadge count={undefined} />);
  expect(container.firstChild).toBeNull();
});
