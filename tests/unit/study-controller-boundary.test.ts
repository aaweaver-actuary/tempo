import { renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useStudyReviewController } from "../../app/features/study";

it("exposes the review projection and semantic actions without mutable controller internals", () => {
  const { result, unmount } = renderHook(() => useStudyReviewController({
    onNextCard: vi.fn(), checkIntegrity: async () => {}, acquireQueue: async () => "installed",
    rememberConfirmed: vi.fn(), isRetryable: () => true,
  }));
  expect(result.current.snapshot.phase).toBe("idle");
  expect(result.current).toEqual(expect.objectContaining({ beginReview: expect.any(Function), submit: expect.any(Function), captureConfirmation: expect.any(Function) }));
  for (const privateName of ["setPhase", "diagnostic", "transition", "pendingEntries", "progressConfirmedReview"]) {
    expect(result.current).not.toHaveProperty(privateName);
  }
  unmount();
});
