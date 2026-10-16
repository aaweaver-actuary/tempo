import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TacticalCatalogPanel } from "../../app/components/tactical-catalog";
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div data-testid="endgame-board" /> }));
vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/endgame-generator", () => ({ generateLegalEndgameFen: () => "k7/8/8/8/8/8/5K2/7R w - - 0 1" }));
vi.mock("../../app/utils/tablebase", () => ({ probeTablebase: vi.fn(async () => ({ category: "draw", moves: [] })), tablebaseCategoryForWhite: () => "draw" }));
beforeEach(() => { vi.resetModules(); vi.stubGlobal("__TEMPO_STUDY__", true); localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it.each(["capture", "delete"])("pr291 R3 unsupported %s rejects before reading retained data, storage mutation or network", async kind => {
  const key = kind === "capture" ? "tempo-pending-tactic-capture-v1" : "tempo-pending-card-delete-v1";
  const bytes = '{"unreadable":"original bytes must survive"}'; localStorage.setItem(key, bytes);
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const read = vi.spyOn(Storage.prototype, "getItem"); const write = vi.spyOn(Storage.prototype, "setItem"); const remove = vi.spyOn(Storage.prototype, "removeItem");
  const request = kind === "capture" ? (await import("../../app/lib/tactic-capture-command")).saveTacticCapture() : (await import("../../app/lib/card-delete-command")).deleteCardCommand("original", 1);
  await expect(request).rejects.toThrow("Workbench");
  expect(fetcher).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled(); expect(remove).not.toHaveBeenCalled(); expect(read.mock.calls.filter(([name]) => name === key)).toEqual([]);
  read.mockRestore(); expect(localStorage.getItem(key)).toBe(bytes);
});
it("pr291 R3 read-only catalog preserves pack selection without activation actions", () => {
  const select = vi.fn();
  const pack = { id: "pack", theme: "fork", group: "basic", difficulty: "easy", ordinal: 1, count: 25 as const, minRating: 0, maxRating: 1000, asset: "pack.json", legacyDeckId: "pack", active: false, clean: 0, introduced: 0, due: 0 };
  render(<TacticalCatalogPanel catalog={{ version: 1, groups: [{ id: "basic", name: "Basic" }], themes: [{ id: "fork", name: "Fork", group: "basic" }], packs: [pack] }} progress={{}} selectedPackId="pack" onSelect={select} busy={false} />);
  expect(screen.queryByRole("button", { name: /activate/i })).toBeNull(); expect(screen.queryByText(/Activate packs to introduce/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Easy.*Pack 1/ })); expect(select).toHaveBeenCalledWith(pack);
});

it("pr291 R3 independent Endgames reads accepted templates and permits local exploration without admission", async () => {
  const bootstrap = { schema_version: 1, workspace_id: "workspace-a", timezone: "America/New_York", local_date: "2026-10-16", settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10, tactics_new_per_day: 5, include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 } };
  const fetcher = vi.fn(async (url: URL | string) => {
    if (String(url).endsWith("/api/study/bootstrap")) return Response.json(bootstrap);
    if (String(url).endsWith("/api/endgames/templates")) return Response.json({ templates: [{ id: "accepted-template", card_id: "accepted-card", name: "Accepted material", white_material: "KBNN", black_material: "K", trained_color: "white", goal_mix: "both", enabled: 1, created_at: "2026-10-16T12:00:00Z" }] });
    throw new Error(`Unexpected request: ${url}`);
  }); vi.stubGlobal("fetch", fetcher);
  const client = await import("../../app/lib/study-connection"); await client.connectStudy("http://learning.test", "workspace-a", "synthetic-credential-" + "x".repeat(32));
  const { default: Endgames } = await import("../../app/views/endgames_view"); render(<Endgames theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "Positions" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /KBNN vs K/ })).toBeTruthy());
  expect(screen.queryByRole("button", { name: /Add to daily training/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Edit material/ })); fireEvent.click(screen.getByRole("button", { name: "Practice material" }));
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/api/endgames/templates"))).toHaveLength(1);
});
