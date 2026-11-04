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

it("settings hydration blocks premature editing then persists the selected prefix length", async () => {
  let finishHydration!: (response: Response) => void;
  const authoritativeSettings = { initial_depth: 6 };
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
  const saveButton = screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement;
  expect(saveButton.disabled).toBe(true);
  fireEvent.change(screen.getByRole("spinbutton", { name: /Default initial prefix length/ }), { target: { value: "9" } });
  expect(screen.queryByText("Unsaved changes")).toBeNull();
  fireEvent.click(saveButton);
  expect(submittedSettings).toHaveLength(0);
  await act(async () => { finishHydration(Response.json(authoritativeSettings)); });
  expect(saveButton.disabled).toBe(false);
  fireEvent.change(screen.getByRole("spinbutton", { name: /Default initial prefix length/ }), { target: { value: "9" } });
  fireEvent.click(saveButton);
  await waitFor(() => expect(submittedSettings).toHaveLength(1));
  const savedRead = await (await fetch("/api/settings")).json();
  expect(savedRead.initial_depth).toBe(9);
});

it("Games settings removes obsolete controls and browser mirroring while global saves preserve compatibility values", async () => {
  const compatibility = { lichess_username: "SavedLichess", chesscom_username: "SavedChesscom", auto_sync_minutes: 17 };
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
  expect(screen.queryByRole("heading", { name: "Existing imported-game analysis" })).toBeNull();
  expect(screen.queryByRole("spinbutton", { name: /Major mistake threshold/ })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Training" }));
  fireEvent.change(screen.getByRole("spinbutton", { name: /Default initial prefix length/ }), { target: { value: "9" } });
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toMatchObject({ initial_depth: 9, ...compatibility });
  expect(reads.mock.calls.filter(([key]) => legacyKeys.includes(key))).toEqual([]);
  expect(writes.mock.calls.filter(([key]) => legacyKeys.includes(key))).toEqual([]);
  for (const key of legacyKeys) expect(localStorage.getItem(key)).toBe("Retained browser value");
});
