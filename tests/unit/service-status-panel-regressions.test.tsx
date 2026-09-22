import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ServiceStatusPanel } from "../../app/components/service-status-panel";
import { browserActivitySnapshot, clearBrowserActivity, subscribeBrowserActivity } from "../../app/lib/browser-activity";
import { runStudyTask } from "../../app/lib/background-study";

afterEach(() => {
  clearBrowserActivity();
  vi.unstubAllGlobals();
});

const activityItem = {
  source: "durable", id: "task-1", title: "Opening graph rebuild", state: "queued",
  phase: "Preparing graph", completed: 4, total: 10,
  updated_at: "2026-09-22T00:00:00Z", error: null, paused: false, promoted: false,
};
const activityResponse = {
  items: [activityItem], counts: { running: 0, queued: 1, paused: 0, failed: 0 },
  total: 1, next_offset: null,
};

describe("background activity tray", () => {
  it("shows truthful progress and lets the user pause and prioritize eligible work", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? Response.json({ ok: true }) : Response.json(activityResponse));
    vi.stubGlobal("fetch", fetchMock);
    render(<ServiceStatusPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Analysis activity/ }));
    expect(await screen.findByText("Opening graph rebuild")).toBeTruthy();
    const progress = screen.getByRole("progressbar", { name: "Opening graph rebuild progress" }) as HTMLProgressElement;
    expect(progress.value).toBe(4);
    expect(progress.max).toBe(10);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/activity/control"),
      expect.objectContaining({ body: JSON.stringify({ source: "durable", id: "task-1", action: "pause" }) }),
    ));
  });

  it("keeps a visible actionable error when the activity service fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Unavailable", { status: 503 })));
    render(<ServiceStatusPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Analysis activity/ }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("HTTP 503"));
    expect(screen.getByRole("button", { name: "Retry status" })).toBeTruthy();
  });

  it("keeps the database writer failure actionable in the new tray", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      ...activityResponse,
      writer: { healthy: false, foreground: 0, background: 0 },
    })));
    render(<ServiceStatusPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Analysis activity" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Restart Tempo"));
  });

  it("retains retry for a failed durable task", async () => {
    const failed = { ...activityItem, state: "failed", phase: "failed", completed: null, total: null, error: "Queue refresh failed" };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST" ? Response.json({ state: "queued" }) : Response.json({
        items: [failed], counts: { running: 0, queued: 0, paused: 0, failed: 1 }, total: 1, next_offset: null,
      }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ServiceStatusPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Analysis activity/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Retry Opening graph rebuild" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/system/tasks/task-1/retry"), { method: "POST" },
    ));
  });

  it("pages a large queue without losing the full activity count", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => Response.json(
      String(input).includes("offset=50")
        ? { items: [{ ...activityItem, id: "task-51", title: "Later analysis" }], counts: { running: 0, queued: 51, paused: 0, failed: 0 }, total: 51, next_offset: null }
        : { items: [activityItem], counts: { running: 0, queued: 51, paused: 0, failed: 0 }, total: 51, next_offset: 50 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    render(<ServiceStatusPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Analysis activity/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByText("Later analysis")).toBeTruthy();
    expect(screen.getByText(/51 queued/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Opening graph rebuild")).toBeTruthy();
  });
});

it("study worker activity reports queued running and completion in order", async () => {
  const states: string[] = [];
  const unsubscribe = subscribeBrowserActivity(() => {
    states.push(browserActivitySnapshot()[0]?.state ?? "empty");
  });
  await runStudyTask({ kind: "lines", lines: [] });
  unsubscribe();
  expect(states).toEqual(["queued", "running", "complete"]);
});
