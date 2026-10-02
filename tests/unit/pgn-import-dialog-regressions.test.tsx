import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImportDialogBox } from "../../app/ImportDialogBox";
import { savePgnImportCommand } from "../../app/lib/pgn-import-command";
import { PendingOperationError } from "../../app/lib/operation-status";
import { reportDebugError } from "../../app/lib/debug-reporting";
import { importResultSchema } from "../../app/domain/schemas";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/pgn-import-command", () => ({ savePgnImportCommand: vi.fn() }));
vi.mock("../../app/lib/debug-reporting", () => ({ reportDebugError: vi.fn() }));
const settings = { initial_depth: 6, timezone: "local", new_cards_per_day: 2, lichess_username: "", chesscom_username: "", auto_sync_minutes: 3, engine_line_window_cp: 30, major_mistake_cp: 100, light_first_interval_days: 7, draw_hold_user_moves: 20 };
const result = importResultSchema.parse({ repertoire_id: "rep", source_name: "opening.pgn", games_found: 1, unique_lines: 1, cards_created: 1, duplicates_merged: 0, cards_admitted_today: 0 });
afterEach(() => { vi.unstubAllGlobals(); vi.mocked(savePgnImportCommand).mockReset(); });

async function openDialog() {
  const databaseUpdated = vi.fn(async () => {});
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/settings")) return Response.json(settings);
    if (url.includes("/queue/window")) return Response.json({ cards: [], projection: { state: "ready", generation: 1, updated_at: "2026-10-02T12:00:01Z", refresh_pending: false, last_error: null } });
    return Response.json({ repertoires: [{ id: "rep", name: "Opening", source_name: "opening.pgn", line_count: 1, card_count: 1, due_count: 1, graph_state: "ready", graph_updated_at: "2026-10-02T12:00:00Z", integrity_status: "clean" }] });
  }));
  const rendered = render(<ImportDialogBox onClose={vi.fn()} onImported={vi.fn()} onViewRepertoire={vi.fn()} onDatabaseUpdated={databaseUpdated} />);
  await screen.findByText("6 user moves");
  await waitFor(() => expect(screen.queryByText("Loading import settings…")).toBeNull());
  fireEvent.change(rendered.container.querySelector('input[type="file"]')!, { target: { files: [new File(["1. e4 e5 2. Nf3 *"], "opening.pgn")] } });
  return { ...rendered, databaseUpdated };
}

it("PGN dialog locks submission and settings while confirmation is working without a failure notice", async () => {
  vi.mocked(savePgnImportCommand).mockImplementation((_file, _color, _depth, options) => new Promise((_resolve, reject) => {
    options!.signal!.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")), { once: true });
  }));
  const dialog = await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await waitFor(() => expect(savePgnImportCommand).toHaveBeenCalledOnce());
  expect((screen.getByRole("button", { name: "Importing…" }) as HTMLButtonElement).disabled).toBe(true);
  expect((dialog.container.querySelector('input[type="file"]') as HTMLInputElement).disabled).toBe(true);
  for (const name of ["White", "Black"])
    expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
  for (const button of dialog.container.querySelectorAll<HTMLButtonElement>(".stepper button"))
    expect(button.disabled).toBe(true);
  expect(screen.queryByRole("alert")).toBeNull();
  dialog.unmount();
});

it("PGN pending timeout is informational and Check again completes the existing import", async () => {
  vi.mocked(savePgnImportCommand).mockRejectedValueOnce(new PendingOperationError("original-import", "Import is still processing. Check again to confirm its result."))
    .mockResolvedValueOnce(result);
  const dialog = await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("status")).textContent).toContain("still processing");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(reportDebugError).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await screen.findByRole("heading", { name: "Imported" });
  expect(dialog.databaseUpdated).toHaveBeenCalledOnce();
  expect(savePgnImportCommand).toHaveBeenCalledTimes(2);
  expect(vi.mocked(savePgnImportCommand).mock.calls[1].slice(0, 3)).toEqual(vi.mocked(savePgnImportCommand).mock.calls[0].slice(0, 3));
});

it("ambiguous PGN delivery offers informational recovery without claiming a failed import", async () => {
  vi.mocked(savePgnImportCommand).mockRejectedValueOnce(new PendingOperationError("original-import", "Import confirmation is unavailable. Check again with the same file and settings."));
  await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("status")).textContent).toContain("confirmation is unavailable");
  expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  expect(reportDebugError).not.toHaveBeenCalled();
});

it("legacy pending PGN dialog retains its diagnostic and selection across Check again", async () => {
  const diagnostic = "Import confirmation is unavailable. Legacy receipt has no saved payload. Recover only from matching journal or outbox evidence; automatic replay is unavailable.";
  vi.mocked(savePgnImportCommand).mockRejectedValue(new PendingOperationError("original-import", diagnostic));
  const dialog = await openDialog();
  const fileInput = dialog.container.querySelector<HTMLInputElement>('input[type="file"]')!;
  const selectedFile = fileInput.files![0];
  fireEvent.click(screen.getByRole("button", { name: "Black" }));
  fireEvent.click(dialog.container.querySelectorAll<HTMLButtonElement>(".stepper button")[1]);
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("status")).textContent).toBe(diagnostic);
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() => expect(savePgnImportCommand).toHaveBeenCalledTimes(2));
  await screen.findByRole("button", { name: "Check again" });
  expect(screen.getByRole("status").textContent).toBe(diagnostic);
  expect(fileInput.files![0]).toBe(selectedFile);
  expect(screen.getByRole("button", { name: "Black" }).classList.contains("active")).toBe(true);
  expect(screen.getByText("7 user moves")).toBeTruthy();
  for (const argumentsUsed of vi.mocked(savePgnImportCommand).mock.calls) {
    expect(argumentsUsed.slice(0, 3)).toEqual([selectedFile, "black", 7]);
    expect(argumentsUsed[3]?.retryBlocked).toBe(false);
  }
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Imported" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry blocked import" })).toBeNull();
  expect(reportDebugError).not.toHaveBeenCalled();
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
});

it("blocked PGN dialog shows its diagnostic and explicitly retries the original operation", async () => {
  vi.mocked(savePgnImportCommand).mockRejectedValueOnce(new PendingOperationError("original-import", "Import is blocked: Write connection unavailable", true))
    .mockResolvedValueOnce(result);
  await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Write connection unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry blocked import" }));
  await screen.findByRole("heading", { name: "Imported" });
  expect(vi.mocked(savePgnImportCommand).mock.calls[1][3]?.retryBlocked).toBe(true);
});

it("failed PGN dialog remains an actionable error and permits a new deliberate attempt", async () => {
  vi.mocked(savePgnImportCommand).mockRejectedValueOnce(new Error("The source admission failed"));
  await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("alert")).textContent).toContain("source admission failed");
  expect(screen.getByRole("button", { name: "Import repertoire" })).toBeTruthy();
  expect(reportDebugError).toHaveBeenCalled();
});

it("closing PGN dialog cancels confirmation without reporting cancellation as a failure", async () => {
  let signal: AbortSignal | undefined;
  vi.mocked(savePgnImportCommand).mockImplementation((_file, _color, _depth, options) => new Promise((_resolve, reject) => {
    signal = options!.signal;
    signal!.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")), { once: true });
  }));
  const dialog = await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await waitFor(() => expect(signal).toBeDefined());
  dialog.unmount();
  expect(signal!.aborted).toBe(true);
  await Promise.resolve();
  expect(reportDebugError).not.toHaveBeenCalled();
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
});
