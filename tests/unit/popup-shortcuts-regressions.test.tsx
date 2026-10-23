import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import SavedLocallyButton from "../../app/components/buttons/SavedLocallyButton";
import { NotificationCenter } from "../../app/components/notification-center";

it("Escape dismisses the local data popup and restores its opener", () => {
  render(<SavedLocallyButton setShowImport={vi.fn()} />);
  const trigger = screen.getByRole("button", { name: "Open local data menu" });
  trigger.focus(); fireEvent.click(trigger);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("menu")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
it("Escape dismisses the notification tray and restores its opener", () => {
  render(<NotificationCenter />);
  const trigger = screen.getByRole("button", { name: "Notifications" });
  trigger.focus(); fireEvent.click(trigger);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
