import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GameSyncSettings } from "../../app/product-composition/GameSyncSettings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const repertoires = [{ id: "white", name: "Synthetic white" }];
const saved = { workspace_id: "legacy", provider: "lichess", account: "Learner",
  window: { start_at: "2026-10-01T00:00:00Z", end_at: "2026-11-01T00:00:00Z", precision: "millisecond" },
  repertoire_ids: ["white"], speeds: ["rapid"], rated: true, interval_seconds: null, revision: 1 };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

it("requires explicit account/window/repertoire/filter choices before persisting", async () => {
  const fetch = vi.fn().mockResolvedValue(response(null));
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).disabled).toBe(false));
  expect((screen.getByLabelText("Rated games") as HTMLSelectElement).value).toBe("");
  expect((screen.getByRole("checkbox", { name: "rapid" }) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  expect(screen.getByRole("alert").textContent).toContain("Choose an account");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("saves only explicit UTC choices and keeps recurrence off unless selected", async () => {
  const fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(response(init?.method === "POST" ? saved : null)));
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText("Game account"), { target: { value: "Learner" } });
  fireEvent.change(screen.getByLabelText("Games from (UTC)"), { target: { value: "2026-10-01T00:00" } });
  fireEvent.change(screen.getByLabelText("Games until (UTC)"), { target: { value: "2026-11-01T00:00" } });
  fireEvent.change(screen.getByLabelText("Rated games"), { target: { value: "rated" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "rapid" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Synthetic white" }));
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  await screen.findByText("Game sync settings saved.");
  const body = JSON.parse(fetch.mock.calls[1][1].body);
  expect(body.expected_revision).toBe(0);
  expect(body.configuration).toMatchObject({ account: "Learner", speeds: ["rapid"], rated: true,
    repertoire_ids: ["white"], interval_seconds: null, revision: 1,
    window: { start_at: "2026-10-01T00:00:00.000Z", end_at: "2026-11-01T00:00:00.000Z" } });
});

it("uses the loaded revision and keeps a concurrent account change visible until refresh", async () => {
  const fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(response(init?.method === "POST" ? { detail: "configuration_changed" } : saved, init?.method === "POST" ? 409 : 200)));
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.change(screen.getByLabelText("Game account"), { target: { value: "Changed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("alert").textContent).toContain("Refresh after it finishes");
  expect(JSON.parse(fetch.mock.calls[1][1].body).expected_revision).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Refresh game sync settings" }));
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
});
