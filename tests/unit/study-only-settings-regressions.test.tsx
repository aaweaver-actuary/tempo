import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SettingsView from "../../app/views/settings_view";
import { saveLocalSettings } from "../../app/lib/settings-save";
import { PendingOperationError } from "../../app/lib/operation-status";
import { useStudyControls } from "../../app/hooks/use-study-only-mode";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/workspace-data", async original => ({
  ...await original<typeof import("../../app/lib/workspace-data")>(),
  readWorkspaceResponse: vi.fn(async () => Response.json({ initial_depth: 6, study_only_mode: false })),
}));
vi.mock("../../app/lib/settings-save", () => ({ saveLocalSettings: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });

function ControlsProbe() {
  const controls = useStudyControls();
  return <output>{controls === null ? "unknown" : String(!controls.studyOnlyMode && controls.repertoirePreparationEnabled)}</output>;
}

it("restoration_unknown_settings_never_authorize_preparation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({})));
  render(<ControlsProbe />);
  await act(async () => undefined);
  expect(screen.getByText("unknown")).toBeTruthy();
});

it("restoration_confirmed_disable_during_settings_read_refetches_without_publishing_stale_controls", async () => {
  let finishOldRead!: (response: Response) => void;
  const fetchSettings = vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => { finishOldRead = resolve; }))
    .mockResolvedValueOnce(Response.json({ study_only_mode: false, repertoire_preparation_enabled: false }));
  vi.stubGlobal("fetch", fetchSettings);
  render(<ControlsProbe />);
  window.dispatchEvent(new Event("tempo:study-mode-changed"));
  await act(async () => finishOldRead(Response.json({ study_only_mode: false, repertoire_preparation_enabled: true })));
  await waitFor(() => expect(screen.getByText("false")).toBeTruthy());
  expect(fetchSettings).toHaveBeenCalledTimes(2);
});

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
