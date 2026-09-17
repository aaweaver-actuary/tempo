import { act, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { OutcomeFlash } from "../../app/components/board-controls";

it("red board feedback disappears after one second even while the failed attempt remains active", () => {
  vi.useFakeTimers();
  const view = render(<OutcomeFlash outcome="wrong" />);
  expect(screen.getByText("×")).toBeTruthy();
  act(() => vi.advanceTimersByTime(999));
  expect(screen.getByText("×")).toBeTruthy();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.queryByText("×")).toBeNull();
  view.rerender(<OutcomeFlash outcome="wrong" />);
  expect(screen.queryByText("×")).toBeNull();
  view.rerender(<OutcomeFlash key="next-attempt" outcome="wrong" />);
  expect(screen.getByText("×")).toBeTruthy();
  act(() => vi.advanceTimersByTime(1000));
  expect(screen.queryByText("×")).toBeNull();
});
