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
  expect((screen.getByLabelText("Collection purpose") as HTMLSelectElement).value).toBe("");
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
  fireEvent.change(screen.getByLabelText("Collection purpose"), { target: { value: "historical" } });
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
  await screen.findByText("Lichess run request accepted; acquisition is pending.");
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
  expect(screen.queryByText("Lichess run request accepted; acquisition is pending.")).toBeNull();
  fireEvent.click(retry);
  await screen.findByText("Lichess run request accepted; acquisition is pending.");
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toBe(posts[0].body);
});

const saveButton = () => screen.getByRole("button", { name: "Save game sync settings" });
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
function mockConfiguration(value: (Omit<typeof saved, "interval_seconds"> & { interval_seconds: number | null }) | null) {
  const fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(response(
    init?.method === "POST" ? JSON.parse(String(init.body)).configuration : value)));
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  return fetch;
}

it.each([60, 86400])("saves an explicitly selected ongoing seed of %i seconds without starting acquisition", async seconds => {
  const fetch = mockConfiguration(saved);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  change("Collection purpose", "ongoing");
  expect((screen.getByLabelText("Ongoing collection interval (seconds)") as HTMLInputElement).value).toBe("");
  change("Games until (UTC)", new Date(Date.parse(saved.window.start_at) + seconds * 1000).toISOString().slice(0, -1));
  fireEvent.click(saveButton());
  expect(screen.getByRole("alert").textContent).toContain("interval of 60–86400");
  expect(fetch).toHaveBeenCalledTimes(1);
  change("Ongoing collection interval (seconds)", "300");
  fireEvent.click(saveButton());
  await screen.findByText("Game sync settings saved.");
  const posts = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(posts[0][0]).toBe("https://games.example/api/product/game-review/configuration");
  expect(JSON.parse(posts[0][1].body)).toMatchObject({ expected_revision: 1, configuration: {
    interval_seconds: 300, revision: 2, account: "Learner", repertoire_ids: ["white"], speeds: ["rapid"], rated: true,
  } });
  expect(screen.getByText(/Saving settings does not start a worker/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh game sync settings" }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
  expect(fetch.mock.calls.filter(([url]) => String(url).endsWith("/sync"))).toHaveLength(0);
});

it.each([59, 86401])("rejects an ongoing seed of %i seconds actionably without changing its dates", async seconds => {
  const end = new Date(Date.parse(saved.window.start_at) + seconds * 1000).toISOString();
  const fetch = mockConfiguration({ ...saved, interval_seconds: 300, window: { ...saved.window, end_at: end } });
  await waitFor(() => expect((screen.getByLabelText("Collection purpose") as HTMLSelectElement).value).toBe("ongoing"));
  fireEvent.click(saveButton());
  expect(screen.getByRole("alert").textContent).toContain("60–86400 seconds");
  expect(screen.getByRole("alert").textContent).toContain("Historical/manual import");
  expect(Date.parse((screen.getByLabelText("Games until (UTC)") as HTMLInputElement).value + "Z")).toBe(Date.parse(end));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("loads an old wide recurring configuration and requires an explicit correction or historical choice", async () => {
  const fetch = mockConfiguration({ ...saved, interval_seconds: 300 });
  await waitFor(() => expect((screen.getByLabelText("Collection purpose") as HTMLSelectElement).value).toBe("ongoing"));
  fireEvent.click(saveButton());
  expect(screen.getByRole("alert").textContent).toContain("60–86400 seconds");
  expect(fetch).toHaveBeenCalledTimes(1);
  change("Games until (UTC)", "2026-10-01T01:00");
  fireEvent.click(saveButton());
  await screen.findByText("Game sync settings saved.");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toMatchObject({ expected_revision: 1,
    configuration: { interval_seconds: 300, revision: 2, window: { end_at: "2026-10-01T01:00:00.000Z" } } });
});

it("preserves historical UTC seconds and milliseconds and turns recurrence off only by explicit choice", async () => {
  const original = { ...saved, interval_seconds: 300, window: { ...saved.window,
    start_at: "2026-10-01T00:00:12.345Z", end_at: "2026-11-01T00:00:56.789Z" } };
  const fetch = mockConfiguration(original);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  change("Collection purpose", "historical");
  fireEvent.click(saveButton());
  await screen.findByText("Game sync settings saved.");
  expect(JSON.parse(fetch.mock.calls[1][1].body).configuration).toMatchObject({ window: original.window, interval_seconds: null });
});

it.each(["", "2026-09-30T00:00", "2026-02-30T00:00"])("rejects an empty, reversed or invalid UTC end %s without posting", async end => {
  const fetch = mockConfiguration(saved);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  change("Games until (UTC)", end);
  fireEvent.click(saveButton());
  expect(screen.getByRole("alert").textContent).toContain("valid UTC dates");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it.each(["direct", "receipt"])("shows actionable backend validation from a %s failure and permits correction", async kind => {
  let operationId = "";
  const detail = "Adjust the ongoing seed to 60–86400 seconds or choose Historical/manual import.";
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Promise.resolve(response({ operation_id: operationId, state: "failed",
      error: { message: "Configuration rejected", status_code: 422, detail } }));
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    operationId = (init.headers as Record<string, string>)["Idempotency-Key"];
    return Promise.resolve(kind === "direct" ? response({ detail }, 422) : response({ operation_id: operationId, state: "queued" }, 202));
  });
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(saveButton());
  expect((await screen.findByRole("alert")).textContent).toBe(detail);
  expect((screen.getByLabelText("Game account") as HTMLInputElement).disabled).toBe(false);
  expect(screen.queryByText("Game sync settings saved.")).toBeNull();
});

it("recovers original ongoing bytes even if the displayed old range is now incompatible", async () => {
  const posts: RequestInit[] = [];
  let operationId = "";
  const original = { ...saved, interval_seconds: 300, window: { ...saved.window, end_at: "2026-10-01T01:00:00Z" } };
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Promise.resolve(new Response(null, { status: 404 }));
    if (init?.method !== "POST") return Promise.resolve(response(posts.length ? { ...saved, interval_seconds: 300, revision: 9 } : original));
    posts.push(init);
    operationId ||= (init.headers as Record<string, string>)["Idempotency-Key"];
    return posts.length === 1 ? Promise.reject(new TypeError("lost ack")) : Promise.resolve(response({ ...original, revision: 2 }));
  });
  vi.stubGlobal("fetch", fetch);
  const first = render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(saveButton());
  await screen.findByRole("alert");
  first.unmount();
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Games until (UTC)") as HTMLInputElement).value).toContain("2026-11-01"));
  expect((screen.getByRole("button", { name: "Refresh game sync settings" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByText("Game sync settings saved.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry settings save" }));
  await screen.findByText("Game sync settings saved.");
  expect(posts[1].body).toBe(posts[0].body);
  expect(posts[1].headers).toEqual(posts[0].headers);
  expect(JSON.parse(String(posts[1].body)).expected_revision).toBe(1);
  expect((posts[1].headers as Record<string, string>)["Idempotency-Key"]).toBe(operationId);
});

it.each(["pending", "complete", "failed"] as const)("reports manual acquisition %s without claiming Study delivery", async status => {
  const fetch = vi.fn().mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(response(init?.method === "POST"
    ? { provider: "lichess", status, operation_id: JSON.parse(String(init.body)).operation_id, error: status === "failed" ? "provider_unavailable" : null }
    : saved)));
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Run saved game sync" }));
  const expected = status === "pending" ? "Lichess run request accepted; acquisition is pending."
    : status === "complete" ? "Lichess acquisition complete. Study delivery is separate." : "Lichess acquisition failed: provider_unavailable.";
  await screen.findByText(expected);
  expect(screen.getByText(/collection-only release does not promise Study reprioritization/)).toBeTruthy();
});

it("keeps an unconfirmed manual command distinct from an accepted acquisition job", async () => {
  let operationId = "";
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/operations/")) return Promise.resolve(response({ operation_id: operationId, state: "queued" }));
    if (init?.method !== "POST") return Promise.resolve(response(saved));
    operationId = JSON.parse(String(init.body)).operation_id;
    return Promise.resolve(response({ operation_id: operationId, state: "queued" }, 202));
  });
  vi.stubGlobal("fetch", fetch);
  render(<GameSyncSettings workspaceId="legacy" repertoires={repertoires} />);
  await waitFor(() => expect((screen.getByLabelText("Game account") as HTMLInputElement).value).toBe("Learner"));
  fireEvent.click(screen.getByRole("button", { name: "Run saved game sync" }));
  expect((await screen.findByRole("alert")).textContent).toContain("pending confirmation");
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry saved game sync" })).toBeTruthy();
});
