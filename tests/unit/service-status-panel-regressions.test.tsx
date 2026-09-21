import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ServiceStatusPanel } from "../../app/components/service-status-panel";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("durable task service status", () => {
  it("terminal task failure is visible and retryable", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Response.json({ state: "queued" });
      }
      return Response.json({
        tasks: [
          {
            id: "failed-task",
            kind: "daily_queue",
            state: "failed",
            phase: "failed",
            attempts: 5,
            max_attempts: 5,
            next_retry: "2026-09-20T00:00:00Z",
            created_at: "2026-09-20T00:00:00Z",
            updated_at: "2026-09-20T00:01:00Z",
            age_seconds: 60,
            last_error: "Queue refresh failed",
          },
        ],
        counts: { queued: 0, active: 0, failed: 1, oldest_queued_age_seconds: 0 },
        writer: { healthy: true, foreground: 0, background: 0 },
        queue_projections: [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ServiceStatusPanel />);
    expect(await screen.findByText("Background work needs attention")).toBeTruthy();
    expect(screen.getByText(/Queue refresh failed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/system/tasks/failed-task/retry"),
        { method: "POST" },
      ),
    );
  });
});
