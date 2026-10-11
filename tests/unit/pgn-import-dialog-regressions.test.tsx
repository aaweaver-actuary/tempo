import { beforeEach as beforeStructuralAdmissionCase } from "vitest";
import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
beforeStructuralAdmissionCase(() => { installPrefixTransitionTestLocks(); });
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImportDialogBox } from "../../app/ImportDialogBox";
import { savePgnImportCommand, discardPendingPgnImport } from "../../app/lib/pgn-import-command";
import { PendingOperationError } from "../../app/lib/operation-status";
import { reportDebugError } from "../../app/lib/debug-reporting";
import { importResultSchema, queueEnvelopeSchema, repertoiresResponseSchema } from "../../app/domain/schemas";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
vi.mock("../../app/lib/pgn-import-command", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/pgn-import-command")>(),
  savePgnImportCommand: vi.fn(), discardPendingPgnImport: vi.fn(),
}));
vi.mock("../../app/lib/debug-reporting", () => ({ reportDebugError: vi.fn() }));
const settings = { initial_depth: 6, timezone: "local", new_cards_per_day: 2, lichess_username: "", chesscom_username: "", auto_sync_minutes: 3, engine_line_window_cp: 30, major_mistake_cp: 100, light_first_interval_days: 7, draw_hold_user_moves: 20 };
const result = importResultSchema.parse({ repertoire_id: "rep", source_name: "opening.pgn", games_found: 1, unique_lines: 1, cards_created: 1, duplicates_merged: 0, cards_admitted_today: 0 });
afterEach(() => { vi.useRealTimers(); localStorage.clear(); vi.unstubAllGlobals(); vi.mocked(savePgnImportCommand).mockReset(); vi.mocked(discardPendingPgnImport).mockReset(); });

it("discarding an unknown PGN import permits a different file after reload", async () => {
  localStorage.setItem("tempo-pending-pgn-import-v1", JSON.stringify({ operationId: "original-import", fingerprint: "old.pgn:white:6:digest" }));
  const dialog = await openDialog(false);
  vi.mocked(discardPendingPgnImport).mockImplementation(async () => {
    localStorage.removeItem("tempo-pending-pgn-import-v1"); return null;
  });
  fireEvent.click(screen.getByRole("button", { name: "Discard pending import" }));
  await screen.findByText("Pending import discarded. You can choose a new PGN file.");
  expect(discardPendingPgnImport).toHaveBeenCalledWith("original-import", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(localStorage.getItem("tempo-pending-pgn-import-v1")).toBeNull();
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  fireEvent.change(dialog.container.querySelector('input[type="file"]')!, { target: { files: [new File(["1. d4 d5 2. Bf4 *"], "different.pgn")] } });
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await screen.findByRole("heading", { name: "Imported" });
  expect(vi.mocked(savePgnImportCommand).mock.calls[0][0].name).toBe("different.pgn");
});

async function openDialog(selectFile = true) {
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
  if (selectFile) fireEvent.change(rendered.container.querySelector('input[type="file"]')!, { target: { files: [new File(["1. e4 e5 2. Nf3 *"], "opening.pgn")] } });
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

it("PGN admission polling yields foreground capacity while graph and queue publication progress", async () => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  await openDialog();
  const fetcher = vi.mocked(fetch);
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await screen.findByRole("heading", { name: "Imported" });
  const polls = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/repertoires") || String(url).includes("/queue/window"));
  expect(polls.length).toBe(3);
  for (const [, options] of polls)
    expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe("background");
});

it("acknowledged PGN import observes publication beyond 250 reads with exactly one actual POST", async () => {
  const actualCommand = await vi.importActual<typeof import("../../app/lib/pgn-import-command")>("../../app/lib/pgn-import-command");
  vi.mocked(savePgnImportCommand).mockImplementation(actualCommand.savePgnImportCommand);
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let publicationReady = false;
  let operationId = "";
  const observationTimes: number[] = [];
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    if (String(input).endsWith("/imports/pgn")) {
      operationId = new Headers(options?.headers).get("Idempotency-Key")!;
      return Response.json({ operation_id: operationId }, { status: 202 });
    }
    if (String(input).includes("/operations/"))
      return Response.json({ operation_id: operationId, state: "complete", response: result });
    if (String(input).includes("/queue/window")) {
      observationTimes.push(Date.now());
      if (!publicationReady)
        return Response.json({ cards: [], projection: { state: "refreshing", generation: 1, updated_at: null, refresh_pending: true, last_error: null } });
    }
    return readyFetch(input, options);
  });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  // Native hashing settles independently; publication is driven by controlled time.
  await act(async () => { await vi.waitFor(() => expect(observationTimes.length).toBeGreaterThan(0)); });
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  const controlledObservationStart = observationTimes.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(12_500); });
  expect(observationTimes.length).toBeGreaterThan(250);
  const controlledTimes = observationTimes.slice(controlledObservationStart - 1);
  expect(controlledTimes.slice(1).map((time, index) => time - controlledTimes[index]))
    .toEqual(Array(controlledTimes.length - 1).fill(50));
  expect(screen.getByRole("status").textContent).toContain("Repertoire imported. Preparing today's queue");
  expect(screen.queryByRole("alert")).toBeNull();
  const importPosts = () => vi.mocked(fetch).mock.calls.filter(([url, options]) =>
    String(url).endsWith("/imports/pgn") && options?.method === "POST");
  expect(importPosts()).toHaveLength(1);
  publicationReady = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  expect(screen.getByRole("heading", { name: "Imported" })).toBeTruthy();
  expect(dialog.databaseUpdated).toHaveBeenCalledOnce();
  expect(importPosts()).toHaveLength(1);
  for (const [, options] of vi.mocked(fetch).mock.calls.filter(([url]) =>
    String(url).endsWith("/repertoires") || String(url).includes("/queue/window")))
    expect(new Headers(options?.headers).get("X-Tempo-Work-Class")).toBe("background");
});

it("closing acknowledged PGN publication cancels its scheduled read without reporting failure", async () => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  vi.mocked(fetch).mockResolvedValue(Response.json({ repertoires: [{ id: "rep", name: "Opening", source_name: "opening.pgn", line_count: 1, card_count: 1, due_count: 0, graph_state: "refreshing" }] }));
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const publicationSignal = vi.mocked(fetch).mock.calls.at(-1)![1]!.signal!;
  fireEvent.click(screen.getByRole("button", { name: "Close import dialog" }));
  expect(publicationSignal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  const readCountAtClose = vi.mocked(fetch).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(fetch).toHaveBeenCalledTimes(readCountAtClose);
  expect(reportDebugError).not.toHaveBeenCalled();
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
});

it.each([
  ["graph", "Repertoire graph failed", { graph_state: "failed" }, undefined],
  ["integrity", "Repertoire integrity scan failed", { integrity_scan_status: "failed", integrity_scan_error: "Worker stopped" }, undefined],
  ["queue", "Daily queue failed", {}, { state: "failed", refresh_pending: false }],
  ["read", "Publication service unavailable", {}, undefined],
] as const)("acknowledged PGN %s failure is actionable and checking publication never resends", async (stage, diagnostic, repertoireChanges, projection) => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    if (stage === "read") return Response.json({ detail: diagnostic }, { status: 503 });
    const response = await readyFetch(input, options);
    const body = await response.json() as { repertoires: Record<string, unknown>[]; projection: Record<string, unknown> };
    if (String(input).endsWith("/repertoires")) Object.assign(body.repertoires[0], repertoireChanges);
    if (projection && String(input).includes("/queue/window")) Object.assign(body.projection, projection);
    return Response.json(body);
  });
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  expect((await screen.findByRole("alert")).textContent).toContain(diagnostic);
  fireEvent.click(screen.getByRole("button", { name: "Check publication" }));
  await screen.findByRole("button", { name: "Check publication" });
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it.each(["close", "unmount"] as const)("%s during an acknowledged publication read cancels and fences late success", async cancellation => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let releaseRead!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input, options) => String(input).includes("/queue/window")
    ? new Promise<Response>(resolve => { releaseRead = resolve; }) : readyFetch(input, options));
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const readSignal = vi.mocked(fetch).mock.calls.at(-1)![1]!.signal!;
  expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toContain("/queue/window");
  if (cancellation === "close") fireEvent.click(screen.getByRole("button", { name: "Close import dialog" }));
  else dialog.unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(readSignal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  const readCount = vi.mocked(fetch).mock.calls.length;
  await act(async () => {
    releaseRead(await readyFetch("/queue/window", {}));
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(fetch).toHaveBeenCalledTimes(readCount);
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
  expect(screen.queryByRole("heading", { name: "Imported" })).toBeNull();
  expect(reportDebugError).not.toHaveBeenCalled();
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it("a reopened import flow cannot be updated by the old publication response", async () => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const oldDialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let releaseOldRead!: (response: Response) => void;
  vi.mocked(fetch).mockImplementation((input, options) => String(input).includes("/queue/window")
    ? new Promise<Response>(resolve => { releaseOldRead = resolve; }) : readyFetch(input, options));
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await waitFor(() => expect(releaseOldRead).toBeTypeOf("function"));
  fireEvent.click(screen.getByRole("button", { name: "Close import dialog" }));
  oldDialog.unmount();
  const newDialog = await openDialog();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await screen.findByRole("heading", { name: "Imported" });
  const newReadCount = vi.mocked(fetch).mock.calls.length;
  await act(async () => { releaseOldRead(await readyFetch("/queue/window", {})); });
  expect(fetch).toHaveBeenCalledTimes(newReadCount);
  expect(oldDialog.databaseUpdated).not.toHaveBeenCalled();
  expect(newDialog.databaseUpdated).toHaveBeenCalledOnce();
  expect(savePgnImportCommand).toHaveBeenCalledTimes(2); // One deliberate command per dialog.
});

it.each(["transport", "body"] as const)("publication %s has a 15-second deadline and a late result cannot publish", async stage => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let releaseRead!: (response: Response) => void;
  let releaseBody!: (body: unknown) => void;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    if (!String(input).includes("/queue/window")) return readyFetch(input, options);
    if (stage === "transport") return new Promise<Response>(resolve => { releaseRead = resolve; });
    const response = await readyFetch(input, options);
    vi.spyOn(response, "json").mockImplementation(() => new Promise(resolve => { releaseBody = resolve; }));
    return response;
  });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(14_999); });
  expect(screen.queryByRole("alert")).toBeNull();
  const readSignal = vi.mocked(fetch).mock.calls.at(-1)![1]!.signal!;
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole("alert").textContent).toContain("timed out after 15 seconds");
  expect(readSignal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  const readCount = vi.mocked(fetch).mock.calls.length;
  await act(async () => {
    const ready = await readyFetch("/queue/window", {});
    if (stage === "transport") releaseRead(ready);
    else releaseBody(await ready.json());
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(fetch).toHaveBeenCalledTimes(readCount);
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it.each(["invalid JSON", "invalid schema", "storage failure"] as const)("acknowledged publication %s stops observation without replaying import", async stage => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  vi.mocked(fetch).mockImplementation(async () => {
    if (stage === "storage failure") throw new Error("Publication storage unavailable");
    return stage === "invalid JSON" ? new Response("{broken") : Response.json({ repertoires: "broken" });
  });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole("alert").textContent).toBeTruthy();
  const readCount = vi.mocked(fetch).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(fetch).toHaveBeenCalledTimes(readCount);
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it("acknowledged publication retries only explicit foreground waits within its read deadline", async () => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let denied = false;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    if (!denied) {
      denied = true;
      return Response.json({ detail: "Waiting for foreground activity" }, { status: 503, headers: { "Retry-After": "1" } });
    }
    return readyFetch(input, options);
  });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(999); });
  expect(screen.queryByRole("heading", { name: "Imported" })).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByRole("heading", { name: "Imported" })).toBeTruthy();
  expect(dialog.databaseUpdated).toHaveBeenCalledOnce();
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it.each(["stale timestamp", "pending refresh"] as const)("publication preserves readiness when queue has %s", async condition => {
  vi.mocked(savePgnImportCommand).mockResolvedValue(result);
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  let ready = false;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    const response = await readyFetch(input, options);
    if (!ready && String(input).includes("/queue/window")) {
      const body = queueEnvelopeSchema.parse(await response.json());
      if (condition === "stale timestamp") body.projection!.updated_at = "2026-10-02T11:59:59Z";
      else body.projection!.refresh_pending = true;
      return Response.json(body);
    }
    return response;
  });
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(dialog.databaseUpdated).not.toHaveBeenCalled();
  expect(screen.queryByRole("heading", { name: "Imported" })).toBeNull();
  ready = true;
  await act(async () => { await vi.advanceTimersByTimeAsync(50); });
  expect(screen.getByRole("heading", { name: "Imported" })).toBeTruthy();
  expect(savePgnImportCommand).toHaveBeenCalledOnce();
});

it("acknowledged import needing integrity repair preserves its existing zero-admission outcome", async () => {
  vi.mocked(savePgnImportCommand).mockResolvedValue({ ...result, integrity: { status: "needs_repair", issue_count: 1, first_issue_id: "issue" } });
  const dialog = await openDialog();
  const readyFetch = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    const response = await readyFetch(input, options);
    if (!String(input).endsWith("/repertoires")) return response;
    const body = repertoiresResponseSchema.parse(await response.json());
    Object.assign(body.repertoires[0], { integrity_status: "needs_repair", graph_state: "refreshing" });
    return Response.json(body);
  });
  const repairRequested = vi.fn();
  window.addEventListener("tempo:integrity", repairRequested);
  try {
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Import repertoire" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole("heading", { name: "Imported" })).toBeTruthy();
    expect(dialog.databaseUpdated).toHaveBeenCalledOnce();
    expect(repairRequested).toHaveBeenCalledOnce();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/queue/window"))).toBe(false);
    expect(savePgnImportCommand).toHaveBeenCalledOnce();
  } finally { window.removeEventListener("tempo:integrity", repairRequested); }
});
