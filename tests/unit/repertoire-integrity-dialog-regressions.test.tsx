import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RepertoireIntegrityDialog } from "../../app/components/repertoire-integrity-dialog";
import { flushIntegrityRepairs, INTEGRITY_REPAIR_CONFIRMED } from "../../app/lib/integrity-repair-outbox";
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
