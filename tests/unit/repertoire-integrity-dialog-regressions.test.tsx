import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RepertoireIntegrityDialog } from "../../app/components/repertoire-integrity-dialog";
import { enqueueIntegrityRepair, discardStaleIntegrityRepair, flushIntegrityRepairs, pendingIntegrityRepairs,
  INTEGRITY_REPAIR_CONFIRMED, INTEGRITY_REPAIRS_CHANGED } from "../../app/lib/integrity-repair-outbox";
import { loadExplorer, type ExplorerResult } from "../../app/lib/lichess-explorer";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/components/move-comparison-table", () => ({
  MoveComparisonTable: ({ onPlay, lichess }: { onPlay: (move: string) => void; lichess: { uci: string }[] }) =>
    <><button onClick={() => onPlay("e2e4")}>Choose e4</button><span data-testid="explorer-moves">{lichess.map(move => move.uci).join(",")}</span></>,
}));
vi.mock("../../app/lib/lichess-explorer", () => ({ loadExplorer: vi.fn(async () => null) }));

const startingFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const issues = ["first", "second"].map(id => ({ id, kind: "multiple_responses",
  fen_key: startingFen.split(" ").slice(0, 4).join(" "), fen: startingFen,
  trained_color: "white", signature: `${id}-signature`, moves: [],
  sources: [{ type: "line", id: `${id}-source` }],
}));
const integrity = { repertoire_id: "rep", status: "needs_repair", issue_count: 2,
  first_issue_id: "first", scan_status: "idle", scan_generation: "scan:1",
  scan_progress: { completed: 2, total: 2 }, last_scan_error: null, issues };

afterEach(() => { localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("repair choice advances to the next conflict while its save request remains pending", async () => {
  let finishRequest: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") return new Promise<Response>(resolve => { finishRequest = resolve; });
    if (String(input).includes("/api/operations/")) return Response.json({ state: "unknown" });
    if (String(input).includes("position-summary")) return Response.json({ moves: [] });
    return Response.json(integrity);
  }));
  const props = { repertoireId: "rep", theme: "brown" as const, pieceSet: "cburnett" as const,
    onClose: vi.fn(), onClean: vi.fn() };
  render(<RepertoireIntegrityDialog {...props} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  const saving = flushIntegrityRepairs();
  try {
    await waitFor(() => expect(screen.getByText(/line second-source/)).not.toBeNull());
    expect((screen.getByRole("button", { name: "Keep this response" }) as HTMLButtonElement).disabled).toBe(true);
    expect(props.onClean).not.toHaveBeenCalled();
    expect(Array.from({ length: localStorage.length }, (_, index) => localStorage.getItem(localStorage.key(index)!))
      .some(value => value?.includes("first-signature") && value.includes("e2e4"))).toBe(true);
    await waitFor(() => expect(finishRequest).toBeDefined());
    fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
    await screen.findByText(/All choices queued/);
  } finally {
    finishRequest?.(Response.json({ operation_id: "pending", state: "pending" }, { status: 202 }));
    await act(async () => { await saving; });
  }
});

it("repair storage failure retains the displayed conflict and selected move", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(integrity)));
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage full"); });
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  expect(screen.getByText(/line first-source/)).not.toBeNull();
  expect(screen.getByText("e2e4")).not.toBeNull();
  expect(screen.getByRole("alert").textContent).toContain("Storage full");
  await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED,
    { detail: { repertoireId: "other" } })); });
  expect(screen.getByRole("alert").textContent).toContain("Storage full");
  expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
});

it("unchanged repair status refresh preserves selection and all queued choices stay distinct from clean", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(integrity)));
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED, { detail: { repertoireId: "other" } })); });
  await waitFor(() => expect(screen.getByText("e2e4")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  await screen.findByText(/line second-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  await screen.findByText(/All choices queued/);
  expect(screen.queryByText("This repertoire is clean.")).toBeNull();
  expect(screen.getByRole("button", { name: "Return to study" })).not.toBeNull();
});

it("late repair evidence from the previous conflict cannot populate the next conflict", async () => {
  let finishPersonal: (response: Response) => void = () => undefined;
  let finishExplorer: (result: ExplorerResult) => void = () => undefined;
  let personalRequests = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("position-summary")) {
      if (++personalRequests === 1) return new Promise<Response>(resolve => { finishPersonal = resolve; });
      return Response.json({ moves: [] });
    }
    return Response.json(integrity);
  }));
  vi.mocked(loadExplorer).mockImplementationOnce(() => new Promise(resolve => { finishExplorer = resolve; }));
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  await screen.findByText(/line second-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  await act(async () => {
    finishPersonal(Response.json({ moves: [{ move_uci: "d2d4", games: 99, score_percentage: 70 }] }));
    finishExplorer({ lichess: { source: "lichess", state: "ready", moves: [{ uci: "d2d4", white: 1, draws: 0, black: 0 }],
      retryable: false, hasCachedData: false }, masters: { source: "masters", state: "ready", moves: [], retryable: false, hasCachedData: false } });
  });
  expect(screen.getByTestId("explorer-moves").textContent).toBe("");
  expect(screen.queryByText(/99 games/)).toBeNull();
  expect(screen.getByText("e2e4")).not.toBeNull();
});

it.each(["queued", "running", "retrying", "failed"])("empty repair issues during a %s scan never claim the repertoire is clean", async scanStatus => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...integrity, issues: [], issue_count: 0,
    first_issue_id: null, scan_status: scanStatus, last_scan_error: "Scan needs attention" })));
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(scanStatus === "failed" ? "Scan needs attention" : "Checking repertoire integrity…");
  expect(screen.queryByText("This repertoire is clean.")).toBeNull();
});

for (const { title, crossTab, refreshFails, changedConflict } of [
  { title: "repair confirmation keeps the next conflict and selection during a delayed refresh", crossTab: false, refreshFails: false, changedConflict: false },
  { title: "repair confirmation keeps the next conflict and selection after a failed refresh", crossTab: false, refreshFails: true, changedConflict: false },
  { title: "cross-tab repair completion keeps the next conflict and selection until fresh evidence arrives", crossTab: true, refreshFails: false, changedConflict: false },
  { title: "fresh changed repair evidence remains reviewable after completion", crossTab: false, refreshFails: false, changedConflict: true },
]) {
  it(title, async () => {
    const pendingRefreshes: ((response: Response) => void)[] = [];
    let initialDialogRead = true;
    let serverEvidence = integrity;
    let submissionConfirmed = false;
    const submission = { task_id: "dialog-repair-graph", repertoire_id: "rep", issue_id: "first", state: "queued" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("position-summary")) return Response.json({ moves: [] });
      if (url.endsWith("/integrity")) {
        if (new Headers(init?.headers).get("X-Tempo-Work-Class") === "background") return Response.json(serverEvidence);
        if (initialDialogRead) { initialDialogRead = false; return Response.json(integrity); }
        return new Promise<Response>(resolve => pendingRefreshes.push(resolve));
      }
      if (url.endsWith("/first/resolve")) { submissionConfirmed = true; return Response.json(submission); }
      if (url.includes("/api/operations/")) return Response.json(submissionConfirmed
        ? { state: "complete", response: submission } : { state: "unknown" });
      if (url.endsWith("/system/tasks")) return Response.json({ tasks: [{ id: "dialog-repair-graph",
        kind: "opening_graph_rebuild", deduplication_key: "rep", generation: 2, state: "complete" }] });
      if (url.endsWith("/repertoires")) return Response.json({ repertoires: [{ id: "rep", name: "Repair repertoire",
        source_name: "fixture.pgn", line_count: 1, card_count: 1, due_count: 1, graph_state: "ready", graph_generation: 2 }] });
      throw new Error(`Unexpected request ${url}`);
    }));
    render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
    await screen.findByText(/line first-source/);
    fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
    await screen.findByText(/line second-source/);
    fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
    await act(async () => { await flushIntegrityRepairs(); });
    const savingRepair = pendingIntegrityRepairs()[0];
    expect(savingRepair.phase).toBe("validating");
    serverEvidence = { ...integrity, issue_count: 1, first_issue_id: "second", issues: issues.slice(1) };
    if (crossTab) {
      const storageKey = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index)!)
        .find(key => localStorage.getItem(key)?.includes(savingRepair.operationId))!;
      const oldValue = localStorage.getItem(storageKey);
      // Another tab removes the confirmed record; this tab receives storage, not its confirmation event.
      await act(async () => {
        localStorage.removeItem(storageKey);
        window.dispatchEvent(new StorageEvent("storage", { key: storageKey, oldValue, newValue: null, storageArea: localStorage }));
      });
    } else {
      // Complete the actual outbox path, including record removal before its changed/confirmation events.
      await act(async () => { await flushIntegrityRepairs(); });
    }
    expect(pendingIntegrityRepairs()).toHaveLength(0);
    expect(screen.queryByText(/line first-source/)).toBeNull();
    expect(screen.getByText(/line second-source/)).not.toBeNull();
    expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
    expect(pendingRefreshes.length).toBeGreaterThan(0);
    if (changedConflict) serverEvidence = { ...integrity, issues: [{ ...issues[0], signature: "first-changed" }, issues[1]] };
    await act(async () => {
      pendingRefreshes.splice(0).forEach(finish => finish(refreshFails
        ? Response.json({ detail: "Integrity refresh unavailable" }, { status: 503 }) : Response.json(serverEvidence)));
    });
    if (changedConflict) {
      expect(screen.getByText(/line first-source/)).not.toBeNull();
      expect(screen.queryByText("e2e4", { selector: "strong" })).toBeNull();
      expect((screen.getByRole("button", { name: "Keep this response" }) as HTMLButtonElement).disabled).toBe(true);
      fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
      fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
      expect(pendingIntegrityRepairs()[0].signature).toBe("first-changed");
      return;
    }
    expect(screen.queryByText(/line first-source/)).toBeNull();
    expect(screen.getByText(/line second-source/)).not.toBeNull();
    expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
    expect((screen.getByRole("button", { name: "Keep this response" }) as HTMLButtonElement).disabled).toBe(false);
    if (refreshFails) {
      expect(screen.getByRole("alert").textContent).toContain("Integrity refresh unavailable");
      await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED,
        { detail: { repertoireId: "other" } })); });
      expect(pendingRefreshes).toHaveLength(1);
      await act(async () => { pendingRefreshes.splice(0).forEach(finish => finish(Response.json(serverEvidence))); });
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.queryByText(/line first-source/)).toBeNull();
      expect(screen.getByText(/line second-source/)).not.toBeNull();
      expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
    }
});
}

it("discarding a stale repair keeps its current conflict and explicit selection reviewable", async () => {
  const saved = enqueueIntegrityRepair({ repertoireId: "rep", issueId: "first", signature: "first-signature", selectedMoveUci: "e2e4" });
  const refreshedEvidence = { ...integrity, issues: [{ ...issues[0], signature: "first-changed" }, issues[1]] };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("/api/operations/")) return Response.json({ state: "unknown" });
    if (String(input).includes("position-summary")) return Response.json({ moves: [] });
    return Response.json(refreshedEvidence);
  }));
  await flushIntegrityRepairs();
  expect(pendingIntegrityRepairs()[0].phase).toBe("stale");
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  await act(async () => { discardStaleIntegrityRepair(saved.operationId); });
  expect(screen.getByText(/line first-source/)).not.toBeNull();
  expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  expect(pendingIntegrityRepairs()[0]).toMatchObject({ issueId: "first", signature: "first-changed", phase: "queued" });
});


it("superseded integrity loads cannot clear a current error or replace newer evidence", async () => {
  const pendingLoads: ((response: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("position-summary")) return Response.json({ moves: [] });
    return new Promise<Response>(resolve => pendingLoads.push(resolve));
  }));
  const props = { repertoireId: "rep", theme: "brown" as const, pieceSet: "cburnett" as const, onClose: vi.fn() };
  const view = render(<RepertoireIntegrityDialog {...props} />);
  await waitFor(() => expect(pendingLoads).toHaveLength(1));
  await act(async () => { pendingLoads.shift()!(Response.json(integrity)); });
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  const reload = async () => { await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED,
    { detail: { repertoireId: "other" } })); }); };
  await reload();
  const oldLoad = pendingLoads.shift()!;
  await reload();
  await act(async () => { pendingLoads.shift()!(Response.json({ detail: "Current load unavailable" }, { status: 503 })); });
  await act(async () => { oldLoad(Response.json({ ...integrity, issues: [], issue_count: 0, first_issue_id: null, status: "clean" })); });
  expect(screen.getByRole("alert").textContent).toContain("Current load unavailable");
  expect(screen.getByText(/line first-source/)).not.toBeNull();
  expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
  await reload();
  const olderFailure = pendingLoads.shift()!;
  await reload();
  await act(async () => { pendingLoads.shift()!(Response.json({ ...integrity, issues: [{ ...issues[0], signature: "changed" }, issues[1]] })); });
  await act(async () => { olderFailure(Response.json({ detail: "Obsolete failure" }, { status: 503 })); });
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByText(/line first-source/)).not.toBeNull();
  expect(screen.queryByText("e2e4", { selector: "strong" })).toBeNull();
  await reload();
  const abortedLoad = pendingLoads.shift()!;
  view.rerender(<RepertoireIntegrityDialog {...props} repertoireId="new-repertoire" />);
  await waitFor(() => expect(pendingLoads).toHaveLength(1));
  await act(async () => { pendingLoads.shift()!(Response.json({ detail: "New repertoire unavailable" }, { status: 503 })); });
  await act(async () => { abortedLoad(Response.json(integrity)); });
  expect(screen.getByRole("alert").textContent).toContain("New repertoire unavailable");
});


it("recovered integrity load clears only its alert while a saved-choice read failure remains visible", async () => {
  let integrityFails = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).includes("position-summary")) return Response.json({ moves: [] });
    return integrityFails ? Response.json({ detail: "Integrity load unavailable" }, { status: 503 }) : Response.json(integrity);
  }));
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett" onClose={vi.fn()} />);
  await screen.findByText(/line first-source/);
  fireEvent.click(screen.getByRole("button", { name: "Choose e4" }));
  const originalGetItem = Storage.prototype.getItem;
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
    if (key.startsWith("tempo-pending-integrity-repair")) throw new Error("Saved journal inaccessible");
    return originalGetItem.call(this, key);
  });
  await act(async () => { window.dispatchEvent(new Event(INTEGRITY_REPAIRS_CHANGED)); });
  expect(screen.getByRole("alert").textContent).toContain("Saved repair choices could not be read");
  integrityFails = true;
  await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED,
    { detail: { repertoireId: "other" } })); });
  expect(screen.getAllByRole("alert")).toHaveLength(2);
  integrityFails = false;
  await act(async () => { window.dispatchEvent(new CustomEvent(INTEGRITY_REPAIR_CONFIRMED,
    { detail: { repertoireId: "other" } })); });
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.getByRole("alert").textContent).toContain("Saved repair choices could not be read");
  expect(screen.queryByText("Integrity load unavailable")).toBeNull();
  expect(screen.getByText("e2e4", { selector: "strong" })).not.toBeNull();
});
