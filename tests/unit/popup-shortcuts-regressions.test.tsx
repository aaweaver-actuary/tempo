import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import SavedLocallyButton from "../../app/components/buttons/SavedLocallyButton";
import { NotificationCenter } from "../../app/components/notification-center";
import BuilderView from "../../app/views/analysis_view";
import { Settings } from "../../app/utils/settings";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/analysis-engines", () => ({ analyzeWithStockfish: vi.fn(async () => []), analyzeWithMaia: vi.fn(async () => []) }));
vi.mock("../../app/lib/engine-broker", () => ({ requestInteractiveAnalysis: vi.fn(async () => []) }));

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

it("Escape dismisses Builder position search despite its dialog boundary", () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ lines: [], cards: [], positions: [] }) })));
  render(<BuilderView imported={[]} settings={new Settings()} theme="brown" pieceSet="cburnett" />);
  fireEvent.click(screen.getByText("Position search"));
  expect(screen.getByRole("dialog", { name: "Position search" })).toBeTruthy();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Position search" })).toBeNull();
});
