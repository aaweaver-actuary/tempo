import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SettingsView from "../../app/views/settings_view";
import { saveLocalSettings } from "../../app/lib/settings-save";
import { PendingOperationError } from "../../app/lib/operation-status";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", async original => ({
  ...await original<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: vi.fn(async () => Response.json({ initial_depth: 6, study_only_mode: false })),
}));
vi.mock("../../app/lib/settings-save", () => ({ saveLocalSettings: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it.each(["pending", "failed"])("study_only_mode_is_effective_only_after_confirmed_settings_save (%s)", async failure => {
  let rejectSave!: (reason: Error) => void;
  vi.mocked(saveLocalSettings).mockReturnValueOnce(new Promise((_, reject) => { rejectSave = reject; }));
  const modeChanged = vi.fn();
  window.addEventListener("tempo:study-mode-changed", modeChanged);
  try {
    render(<SettingsView theme="brown" pieceSet="cburnett" sound={false}
      onTheme={vi.fn()} onPieces={vi.fn()} onSound={vi.fn()} />);
    const save = screen.getByRole("button", { name: "Save settings" });
    await waitFor(() => expect((save as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole("checkbox", { name: /Study-only mode/ }));
    expect(modeChanged).not.toHaveBeenCalled();
    fireEvent.click(save);
    await waitFor(() => expect(saveLocalSettings).toHaveBeenCalledWith(expect.objectContaining({ study_only_mode: true })));
    expect(modeChanged).not.toHaveBeenCalled();
    await act(async () => rejectSave(failure === "pending" ? new PendingOperationError("mode-save") : new Error("Save failed")));
    expect(modeChanged).not.toHaveBeenCalled();
    vi.mocked(saveLocalSettings).mockResolvedValueOnce();
    fireEvent.click(save);
    await waitFor(() => expect(modeChanged).toHaveBeenCalledTimes(1));
  } finally { window.removeEventListener("tempo:study-mode-changed", modeChanged); }
});
