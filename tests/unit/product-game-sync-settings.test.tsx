import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GameSyncSettings } from "../../app/product-composition/GameSyncSettings";

vi.hoisted(() => vi.resetModules());
vi.mock("../../app/const", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/const")>(), API_URL: "https://games.example",
}));
vi.mock("../../app/lib/study-connection", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/study-connection")>(), LEARNING_URL: "https://learning.example",
}));

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
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

it("replays the identical settings save after a lost acknowledgment without permitting edited payloads", async () => {
  const posts: RequestInit[] = [];
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Promise.resolve(new Response(null, { status: 404 }));
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    posts.push(init);
    return posts.length === 1 ? Promise.reject(new TypeError("fetch failed")) : Promise.resolve(response({ ...saved, revision: 2 }));
  });
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("Game account") as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByLabelText("Game provider") as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry settings save" }));
  await screen.findByText("Game sync settings saved.");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toBe(posts[0].body);
  expect(posts[1].headers).toEqual(posts[0].headers);
  expect((posts[0].headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
});

it("shows pending save honestly and reads the original completed receipt before accepting it", async () => {
  let operationId = "";
  let checks = 0;
  const receiptUrls: string[] = [];
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) {
      receiptUrls.push(url);
      checks += 1;
      return Promise.resolve(response(checks === 1 ? { operation_id: operationId, state: "queued" }
        : { operation_id: operationId, state: "complete", response: { ...saved, revision: 2 } }));
    }
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    operationId = (init.headers as Record<string, string>)["Idempotency-Key"];
    return Promise.resolve(response({ operation_id: operationId, state: "queued" }, 202));
  });
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  await screen.findByText("Game sync settings are still pending. Retry the original save to check its result.");
  expect(screen.queryByText("Game sync settings saved.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry settings save" }));
  await screen.findByText("Game sync settings saved.");
  expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(receiptUrls).toEqual(Array(2).fill(`https://games.example/api/operations/${encodeURIComponent(operationId)}`));
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith("https://learning.example"))).toBe(false);
});

it("keeps a manual sync identity through transport failure and makes an acknowledged pending job explicit", async () => {
  const posts: RequestInit[] = [];
  const fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    posts.push(init);
    return posts.length === 1 ? Promise.reject(new TypeError("fetch failed")) : Promise.resolve(response({
      provider: "lichess", status: "pending", operation_id: JSON.parse(String(init.body)).operation_id, error: null,
    }));
  });
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Run saved game sync" }));
  await screen.findByRole("alert");
  expect((screen.getByLabelText("Game provider") as HTMLSelectElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry saved game sync" }));
  await screen.findByText("Lichess sync: pending.");
  expect(posts[1].body).toBe(posts[0].body);
});

it("recovers the original lost-ack settings operation and immutable bytes after the Games tab remounts", async () => {
  const posts: RequestInit[] = [];
  const fetch = vi.fn((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Promise.resolve(new Response(null, { status: 404 }));
    if (init?.method !== "POST") return Promise.resolve(response({ ...saved, revision: posts.length ? 2 : 1 }));
    posts.push(init);
    return posts.length === 1 ? Promise.reject(new TypeError("acknowledgment lost"))
      : Promise.resolve(response({ ...saved, revision: 2 }));
  });
  vi.stubGlobal("fetch", fetch);
  const first = render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Save game sync settings" }));
  await screen.findByRole("alert");
  first.unmount();
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  const retry = await screen.findByRole("button", { name: "Retry settings save" });
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  expect((screen.getByLabelText("Game account") as HTMLInputElement).disabled).toBe(true);
  expect(screen.queryByText("Game sync settings saved.")).toBeNull();
  fireEvent.click(retry);
  await screen.findByText("Game sync settings saved.");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toBe(posts[0].body);
  expect(posts[1].headers).toEqual(posts[0].headers);
  expect(JSON.parse(String(posts[1].body)).expected_revision).toBe(1);
});

it("recovers the same lost-ack manual run after unmount without creating a second operation", async () => {
  const posts: RequestInit[] = [];
  const fetch = vi.fn((_url: string, init?: RequestInit) => {
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    posts.push(init);
    return posts.length === 1 ? Promise.reject(new TypeError("acknowledgment lost")) : Promise.resolve(response({
      provider: "lichess", status: "pending", operation_id: JSON.parse(String(init.body)).operation_id, error: null,
    }));
  });
  vi.stubGlobal("fetch", fetch);
  const first = render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Run saved game sync" }));
  await screen.findByRole("alert");
  first.unmount();
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  const retry = await screen.findByRole("button", { name: "Retry saved game sync" });
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  expect((screen.getByLabelText("Game provider") as HTMLSelectElement).disabled).toBe(true);
  expect(screen.queryByText("Lichess sync: pending.")).toBeNull();
  fireEvent.click(retry);
  await screen.findByText("Lichess sync: pending.");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toBe(posts[0].body);
});
