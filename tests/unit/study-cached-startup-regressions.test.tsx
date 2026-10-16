import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { IDBFactory } from "fake-indexeddb";
vi.mock("../../app/views/home_view", () => ({ default: () => <div>Cached Study fixture</div> }));
vi.mock("../../app/components/study-settings-panel", () => ({ default: () => null }));
const bootstrap = { schema_version: 1, workspace_id: "workspace-a", timezone: "America/New_York", local_date: "2026-10-16", settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10, tactics_new_per_day: 5, include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 } };
beforeEach(() => { vi.resetModules(); vi.stubGlobal("__TEMPO_STUDY__", true); vi.stubGlobal("indexedDB", new IDBFactory()); localStorage.clear(); sessionStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function setup(change?: (client: typeof import("../../app/lib/study-connection")) => void) {
  const client = await import("../../app/lib/study-connection");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(bootstrap)));
  await client.connectStudy("http://learning.test", "workspace-a", "synthetic-credential-" + "x".repeat(32));
  change?.(client);
  return client;
}
async function mount() { const { default: Boundary } = await import("../../app/components/study-connection-boundary"); render(<Boundary />); }
it.each([true, false])("pr291 R1 transport outage opens validated cached practice when navigator.onLine=%s", async online => {
  await setup(); vi.spyOn(navigator, "onLine", "get").mockReturnValue(online);
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await mount(); expect(await screen.findByText("Cached Study fixture")).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("Learning is unavailable");
});
it.each([401, 403, 500])("pr291 R1 HTTP %s never authorizes cached startup", async status => {
  await setup(); vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));
  const retained = localStorage.getItem("tempo-learning-authorities-v1");
  await mount(); await screen.findByRole("alert"); expect(screen.queryByText("Cached Study fixture")).toBeNull();
  expect(localStorage.getItem("tempo-learning-authorities-v1")).toBe(retained);
});
it.each(["workspace", "schema", "timezone"])("pr291 R1 invalid cached %s cannot authorize transport fallback", async invalid => {
  await setup(client => { const saved = client.studyConnection()!; Object.assign(saved.bootstrap!, invalid === "workspace" ? { workspace_id: "wrong" } : invalid === "schema" ? { schema_version: 2 } : { timezone: "Not/AZone" }); });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await mount(); await screen.findByRole("alert"); expect(screen.queryByText("Cached Study fixture")).toBeNull();
});
it("pr291 R1 storage failure is not transport availability", async () => {
  await setup(); localStorage.setItem("tempo-learning-authorities-v1", "corrupt");
  const fetcher = vi.fn().mockRejectedValue(new TypeError("Failed to fetch")); vi.stubGlobal("fetch", fetcher);
  await mount(); await screen.findByRole("alert"); expect(screen.queryByText("Cached Study fixture")).toBeNull(); expect(fetcher).not.toHaveBeenCalled();
});
it("pr291 R1 first connection without cache stays on the connection screen", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
  await mount(); await waitFor(() => expect(screen.getByRole("heading", { name: "Connect Study to Learning" })).toBeTruthy()); expect(screen.queryByText("Cached Study fixture")).toBeNull();
});
