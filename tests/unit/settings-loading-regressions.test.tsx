import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SettingsView from "../../app/views/settings_view";
import { readWorkspaceResponse } from "../../app/lib/workspace-data";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: vi.fn(),
}));
afterEach(() => vi.unstubAllGlobals());

it("settings hydration blocks premature editing then persists independent defensive toggles", async () => {
  let finishHydration!: (response: Response) => void;
  const authoritativeSettings = { initial_depth: 6, defensive_analysis_enabled: false, include_defensive_cards_in_daily_stack: true };
  vi.mocked(readWorkspaceResponse).mockReturnValue(new Promise(resolve => { finishHydration = resolve; }));
  let persistedSettings = authoritativeSettings;
  const submittedSettings: typeof authoritativeSettings[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_input, options?: RequestInit) => {
    if (options?.method === "PUT") {
      persistedSettings = JSON.parse(String(options.body));
      submittedSettings.push(persistedSettings);
    }
    return Response.json(persistedSettings);
  }));
  render(<SettingsView theme="brown" pieceSet="cburnett" sound={false}
    onTheme={vi.fn()} onPieces={vi.fn()} onSound={vi.fn()} />);
  await waitFor(() => expect(readWorkspaceResponse).toHaveBeenCalled());
  const analysisToggle = screen.getByRole("checkbox", { name: /^Defensive analysis/ }) as HTMLInputElement;
  const cardsToggle = screen.getByRole("checkbox", { name: /Defensive cards in daily stack/ }) as HTMLInputElement;
  const saveButton = screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement;
  expect(saveButton.disabled).toBe(true);
  expect(analysisToggle.disabled).toBe(true);
  expect(cardsToggle.disabled).toBe(true);
  fireEvent.click(analysisToggle);
  fireEvent.click(cardsToggle);
  fireEvent.change(screen.getByRole("spinbutton", { name: /Default initial prefix length/ }), { target: { value: "9" } });
  expect(screen.queryByText("Unsaved changes")).toBeNull();
  fireEvent.click(saveButton);
  expect(submittedSettings).toHaveLength(0);
  await act(async () => { finishHydration(Response.json(authoritativeSettings)); });
  expect(saveButton.disabled).toBe(false);
  expect(analysisToggle.disabled).toBe(false);
  expect(cardsToggle.disabled).toBe(false);
  fireEvent.click(analysisToggle);
  fireEvent.click(cardsToggle);
  fireEvent.click(saveButton);
  await waitFor(() => expect(submittedSettings).toHaveLength(1));
  expect([submittedSettings[0].defensive_analysis_enabled, submittedSettings[0].include_defensive_cards_in_daily_stack]).toEqual([true, false]);
  const savedRead = await (await fetch("/api/settings")).json();
  expect(savedRead).toMatchObject({ defensive_analysis_enabled: true, include_defensive_cards_in_daily_stack: false });
});
