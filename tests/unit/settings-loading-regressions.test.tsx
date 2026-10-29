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

it("Games settings removes obsolete controls and browser mirroring while global saves preserve compatibility values", async () => {
  const compatibility = { lichess_username: "SavedLichess", chesscom_username: "SavedChesscom", auto_sync_minutes: 17, major_mistake_cp: 275 };
  const authoritativeSettings = { initial_depth: 6, ...compatibility };
  vi.mocked(readWorkspaceResponse).mockImplementation(async () => Response.json(authoritativeSettings));
  const legacyKeys = ["tempo-lichess-username", "tempo-chesscom-username"];
  for (const key of legacyKeys) localStorage.setItem(key, "Retained browser value");
  const reads = vi.spyOn(Storage.prototype, "getItem");
  const writes = vi.spyOn(Storage.prototype, "setItem");
  const submitted: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = new URL(String(input), window.location.href).pathname;
    if (path === "/api/settings" && options?.method === "PUT") {
      submitted.push(JSON.parse(String(options.body)));
      return Response.json(submitted.at(-1));
    }
    if (path === "/api/repertoires") return Response.json({ repertoires: [{ id: "synthetic", name: "Synthetic repertoire" }] });
    if (path === "/api/product/game-review/configuration/lichess") return Response.json(null);
    throw new Error(`Unexpected synthetic request: ${path}`);
  }));
  render(<SettingsView theme="brown" pieceSet="cburnett" sound={false}
    onTheme={vi.fn()} onPieces={vi.fn()} onSound={vi.fn()} />);
  const save = screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement;
  await waitFor(() => expect(save.disabled).toBe(false));
  fireEvent.click(screen.getByRole("tab", { name: "Games" }));
  await screen.findByRole("region", { name: "Game sync settings" });
  expect(screen.getByRole("button", { name: "Save game sync settings" })).toBeTruthy();
  expect(screen.queryByLabelText("Lichess username")).toBeNull();
  expect(screen.queryByLabelText("Chess.com username")).toBeNull();
  expect(screen.queryByLabelText(/Automatic sync/)).toBeNull();
  expect(screen.getByRole("heading", { name: "Existing imported-game analysis" })).toBeTruthy();
  expect(screen.getByText(/Controls existing imported-game analysis and finding classification/)).toBeTruthy();
  const threshold = screen.getByRole("spinbutton", { name: /Major mistake threshold/ }) as HTMLInputElement;
  expect([threshold.value, threshold.min, threshold.max]).toEqual(["275", "25", "1000"]);
  fireEvent.click(screen.getByRole("tab", { name: "Training" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: /Default initial prefix length/ }), { target: { value: "9" } });
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toMatchObject({ initial_depth: 9, ...compatibility });
  expect(reads.mock.calls.filter(([key]) => legacyKeys.includes(key))).toEqual([]);
  expect(writes.mock.calls.filter(([key]) => legacyKeys.includes(key))).toEqual([]);
  for (const key of legacyKeys) expect(localStorage.getItem(key)).toBe("Retained browser value");
});
