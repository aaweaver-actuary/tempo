import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TacticalCatalogPanel } from "../../app/components/tactical-catalog";
vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div data-testid="endgame-board" /> }));
vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/utils/tablebase", () => ({ probeTablebase: vi.fn(async () => ({ category: "draw", moves: [] })) }));
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

it("independent Endgames preserves saved practice without calculation or authoring requests", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const { default: Endgames } = await import("../../app/views/endgames_view");
  render(<Endgames theme="brown" pieceSet="cburnett" onQueueChanged={vi.fn()} />);
  expect(screen.getByRole("status").textContent).toContain("calculation has been removed");
  expect(screen.queryByRole("button", { name: /Add to daily training|Edit material|Practice material/ })).toBeNull();
  expect(fetcher).not.toHaveBeenCalled();
});
