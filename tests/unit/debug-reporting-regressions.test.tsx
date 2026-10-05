import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import {
  buildDebugBundle,
  clearDebugErrors,
  debugErrors,
  installGlobalDebugErrorHandlers,
  reportDebugError,
  setActiveDebugWorkspace,
} from "../../app/lib/debug-reporting";
import { setBackgroundDiagnostics } from "../../app/lib/service-status";
import { DebugErrorPanel } from "../../app/components/debug-error-panel";
import { TempoErrorBoundary } from "../../app/components/error-boundary";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { useTrainingStore } from "../../app/state/training-store";
import { STANDARD_FEN } from "../../app/const";
import { buildNotificationExport, notifications } from "../../app/lib/notifications";

beforeEach(() => {
  clearDebugErrors();
  setActiveDebugWorkspace("train");
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true);
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
});

describe("frontend debug reporting", () => {
  it("review debug export retains failed A identity and machine classification independently of the visible board", () => {
    const identity = { source: "training-review-replay", endpoint: "/api/cards/card-a/review/reconcile",
      cardId: "card-a", queueEntryId: 17, attemptId: "completed-a", status: 409,
      code: "card_revision_changed", classification: "conflict" as const, retryable: false };
    const record = reportDebugError(new Error("Earlier completed result needs review"), identity);
    const bundle = JSON.parse(buildDebugBundle(record.id));
    const { endpoint, ...contextIdentity } = identity;
    expect(bundle.error.context).toMatchObject({ ...contextIdentity, endpointPath: endpoint });
    expect(bundle.recentErrors[0]).toMatchObject({ cardId: "card-a", queueEntryId: 17,
      attemptId: "completed-a", status: 409, code: "card_revision_changed", classification: "conflict", retryable: false });
  });
  it("removes canary secrets from every persisted and exported incident field", () => {
    const canaries = ["canary-password-42", "canary-api-key-42", "canary-bearer-42", "canary-url-42"];
    reportDebugError(new Error(
      `password=${canaries[0]} api_key=${canaries[1]} Bearer ${canaries[2]} ` +
      `https://person:${canaries[3]}@tempo.example/path?token=${canaries[3]}`,
    ), { source: "sync", endpoint: "/api/games/sync/status" });
    const serialized = `${localStorage.getItem("tempo-notifications-v1")}${buildNotificationExport("error")}`;
    for (const canary of canaries) expect(serialized).not.toContain(canary);
    expect(notifications()[0].key).toMatch(/^debug-incident:[a-f0-9]+$/);
  });
  it("builds a bounded redacted bundle from the active board and training state", () => {
    useBoardShellStore.getState().setShellBoardForOwner("builder", {
      ...useBoardShellStore.getState().board,
      fen: STANDARD_FEN,
      orientation: "black",
      interactionMode: "readonly",
      positionRevision: 4,
    });
    setActiveDebugWorkspace("builder");
    const record = reportDebugError(
      new Error("Request failed for player@example.com?token=secret"),
      {
        kind: "api",
        source: "test",
        endpoint: "http://127.0.0.1:8000/api/games?token=secret",
        method: "GET",
        status: 503,
      },
    );

    const payload = JSON.parse(buildDebugBundle(record.id)) as {
      error: { message: string; context: { endpointPath?: string } };
      environment: { path: string };
      workspace: { activeView: string; board: { orientation: string }; training: { currentFen: string } };
      omitted: string[];
    };
    expect(payload.error.message).not.toContain("player@example.com");
    expect(payload.error.message).not.toContain("token=secret");
    expect(payload.error.context.endpointPath).toBe("/api/games");
    expect(payload.environment.path).toBe("/");
    expect(payload.workspace.activeView).toBe("builder");
    expect(payload.workspace.board.orientation).toBe("black");
    expect(payload.workspace.training.currentFen).toBe(STANDARD_FEN);
    expect(payload.omitted.join(" ")).toContain("PGN");
  });

  it("deduplicates immediate repeats and bounds retained errors", () => {
    const first = reportDebugError(new Error("same failure"), { source: "test" });
    const duplicate = reportDebugError(new Error("same failure"), { source: "test" });
    expect(duplicate.id).toBe(first.id);
    expect(reportDebugError(new Error("same failure"), { source: "other" }).id).not.toBe(first.id);
    for (let index = 0; index < 25; index += 1)
      reportDebugError(new Error(`failure ${index}`), { source: "test" });
    expect(debugErrors()).toHaveLength(20);
    expect(JSON.parse(buildDebugBundle()).recentErrors).toHaveLength(10);
  });

  it("does not reuse notification identity after debug module reload", async () => {
    const first = reportDebugError(new Error("first failure"), { source: "sync", endpoint: "/api/games/sync/status" });
    vi.resetModules();
    const fresh = await import("../../app/lib/debug-reporting");
    const freshNotifications = await import("../../app/lib/notifications");
    const second = fresh.reportDebugError(new Error("second failure"), { source: "sync", endpoint: "/api/games/sync/status" });
    expect(second.id).not.toBe(first.id);
    expect(freshNotifications.notifications().map((record) => record.message)).toContain("first failure");
    expect(freshNotifications.notifications().map((record) => record.message)).toContain("second failure");
  });

  it("repeated status polling updates one incident without another toast", () => {
    vi.useFakeTimers();
    try {
      reportDebugError(new Error("invalid sync payload"), {
        kind: "data-validation", source: "validated-data", endpoint: "/api/games/sync/status",
      });
      vi.advanceTimersByTime(2_000);
      reportDebugError(new Error("invalid sync payload"), {
        kind: "data-validation", source: "validated-data", endpoint: "/api/games/sync/status",
      });
      expect(notifications().filter((record) => record.message === "invalid sync payload")).toHaveLength(1);
      expect(notifications().find((record) => record.message === "invalid sync payload")?.occurrenceCount).toBe(2);
    } finally { vi.useRealTimers(); }
  });

  it("captures window exceptions and unhandled rejections", () => {
    const cleanup = installGlobalDebugErrorHandlers();
    window.dispatchEvent(new ErrorEvent("error", { error: new Error("window boom") }));
    const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(rejection, "reason", { value: new Error("promise boom") });
    window.dispatchEvent(rejection);
    expect(debugErrors().map((record) => record.message)).toEqual([
      "window boom",
      "promise boom",
    ]);
    cleanup?.();
  });

  it("Safari extension fixinator rejection stays in diagnostics without a Tempo error panel", async () => {
    const cleanup = installGlobalDebugErrorHandlers();
    const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(rejection, "reason", { value: "Error: Looks like there is an error in the background page. undefined is not an object (evaluating 'window.fixinatorInputs.has')" });
    window.dispatchEvent(rejection);
    expect(debugErrors().at(-1)?.context.source).toBe("browser-extension");
    render(<DebugErrorPanel />);
    expect(screen.queryByText("Tempo encountered an error")).toBeNull();
    expect(buildDebugBundle()).toContain("fixinatorInputs");
    const ordinaryRejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(ordinaryRejection, "reason", { value: new Error("ordinary promise failure") });
    window.dispatchEvent(ordinaryRejection);
    await waitFor(() => expect(screen.getByText("Tempo encountered an error")).toBeTruthy());
    expect(debugErrors().at(-1)?.context.source).toBe("window.unhandledrejection");
    cleanup?.();
  });

  it("iPhone script error retains safe source coordinates without inventing a cause", () => {
    const cleanup = installGlobalDebugErrorHandlers();
    window.dispatchEvent(new ErrorEvent("error", {
      message: "Script error.", filename: "https://tempo.example/assets/app.js?token=secret",
      lineno: 42, colno: 7,
    }));
    const bundle = JSON.parse(buildDebugBundle()) as {
      error: { message: string; context: { scriptPath: string; line: number; column: number } };
    };
    expect(bundle.error.message).toBe("Script error.");
    expect(bundle.error.context).toMatchObject({ scriptPath: "/assets/app.js", line: 42, column: 7 });
    expect(JSON.stringify(bundle)).not.toContain("token=secret");
    cleanup?.();
  });

  it("iPhone script error without source stays explicit about missing evidence", () => {
    const cleanup = installGlobalDebugErrorHandlers();
    window.dispatchEvent(new ErrorEvent("error", { message: "Script error." }));
    const bundle = JSON.parse(buildDebugBundle()) as {
      error: { message: string; context: { scriptPath?: string; line?: number } };
    };
    expect(bundle.error.message).toBe("Script error.");
    expect(bundle.error.context.scriptPath).toBeUndefined();
    expect(bundle.error.context.line).toBeUndefined();
    cleanup?.();
  });

  it("shows a copy action and reports clipboard success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    reportDebugError(new Error("copy me"), { source: "test" });
    render(<DebugErrorPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Copy debug info" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Copied debug info" })).toBeTruthy());
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('"schemaVersion": 1'));
  });

  it("renders a selectable payload when clipboard copying is unavailable", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    Object.defineProperty(document, "execCommand", {
      configurable: true,
      value: vi.fn(() => false),
    });
    reportDebugError(new Error("manual copy"), { source: "test" });
    render(<DebugErrorPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Copy debug info" }));
    await waitFor(() => expect(screen.getByText(/Clipboard access was unavailable/)).toBeTruthy());
    expect(screen.getByRole("textbox", { name: "Debug information" })).toBeTruthy();
  });

  it("shows the copyable fallback after a render failure", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    function BrokenComponent(): ReactElement {
      throw new Error("render boom");
    }
    render(
      <TempoErrorBoundary>
        <BrokenComponent />
      </TempoErrorBoundary>,
    );
    await waitFor(() => expect(screen.getByText("Tempo encountered an error")).toBeTruthy());
    expect(buildDebugBundle()).toContain("render boom");
    consoleError.mockRestore();
  });
});

it("debug bundle includes only validated aggregate background diagnostics", async () => {
  setBackgroundDiagnostics({ generated_at: "2026-10-02T12:00:00Z", window_start: "2026-10-01T12:05:00Z", window_end: "2026-10-02T12:00:00Z",
    available: false, unavailable_reason: "query_deadline", query_duration_seconds: 0.1, runtime: {} });
  const bundle = JSON.parse(buildDebugBundle());
  expect(bundle.backgroundDiagnostics.available).toBe(false);
  expect(bundle.backgroundDiagnostics.unavailable_reason).toBe("query_deadline");
  expect(() => setBackgroundDiagnostics({ ...bundle.backgroundDiagnostics, payload: { token: "canary-secret" } })).toThrow();
  expect(buildDebugBundle()).not.toContain("canary-secret");
  setBackgroundDiagnostics(null);
});
