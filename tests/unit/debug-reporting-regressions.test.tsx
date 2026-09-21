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
import { DebugErrorPanel } from "../../app/components/debug-error-panel";
import { TempoErrorBoundary } from "../../app/components/error-boundary";
import { useBoardShellStore } from "../../app/state/board-shell-store";
import { useTrainingStore } from "../../app/state/training-store";
import { STANDARD_FEN } from "../../app/const";

beforeEach(() => {
  clearDebugErrors();
  setActiveDebugWorkspace("train");
  useBoardShellStore.setState(useBoardShellStore.getInitialState(), true);
  useTrainingStore.setState(useTrainingStore.getInitialState(), true);
});

describe("frontend debug reporting", () => {
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
    const duplicate = reportDebugError(new Error("same failure"), { source: "other" });
    expect(duplicate.id).toBe(first.id);
    for (let index = 0; index < 25; index += 1)
      reportDebugError(new Error(`failure ${index}`), { source: "test" });
    expect(debugErrors()).toHaveLength(20);
    expect(JSON.parse(buildDebugBundle()).recentErrors).toHaveLength(10);
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
