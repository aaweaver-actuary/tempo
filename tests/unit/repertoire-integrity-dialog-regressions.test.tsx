import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { RepertoireIntegrityDialog } from "../../app/components/repertoire-integrity-dialog";

vi.mock("../../app/components/board/chessboard", async () => {
  const { KeyboardTestBoard } = await import("./keyboard-board-fixture");
  return { Chessboard: (props: React.ComponentProps<typeof KeyboardTestBoard>) =>
    <KeyboardTestBoard {...props} testId="repair-board" /> };
});
vi.mock("../../app/lib/lichess-explorer", () => ({ loadExplorer: async () => undefined }));

const fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const issue = { id: "issue", signature: "signature", kind: "multiple_responses",
  fen, fen_key: fen.split(" ").slice(0, 4).join(" "), trained_color: "white",
  moves: [{ uci: "e2e4", line_count: 1, card_count: 1, review_count: 2 },
    { uci: "d2d4", line_count: 1, card_count: 1, review_count: 0 }], sources: [] };
export const repairPayload = { repertoire_id: "rep", status: "needs_repair", issue_count: 1,
  first_issue_id: "issue", scan_status: "idle", scan_generation: "scan:1",
  scan_progress: { completed: 2, total: 2 }, last_scan_error: null, issues: [issue] };
const preview = { state: "ready", repertoire_id: "rep", issue_id: "issue", signature: "signature",
  starting_fen: fen, trained_color: "white", route_start_fen: fen, route_uci: [],
  suggested_move_uci: "e2e4", suggestion_reason: "exact transposition", reason: null,
  engine_lines: [{ move_uci: "e2e4", score: { cp: 25, mate: null }, loss_cp: 0, depth: 14 }],
  candidates: [{ move_uci: "e2e4", score: { cp: 25, mate: null }, loss_cp: 0,
    similarity: "exact transposition", repertoire_line_count: 1, exact_transposition: true, example_line_id: "one", example_line_name: "one",
    engine_version: "stockfish", network_version: "nnue", depth: 14, report_id: "report", source_type: "line", source_id: "one", source_ply: 0, preview_moves_uci: ["e2e4", "e7e5", "g1f3"] }] };

beforeEach(() => { localStorage.clear(); vi.unstubAllGlobals(); });
function fetchFixture() {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/recommendations")) return Response.json(preview);
    if (url.includes("/position-summary")) return Response.json({ moves: [] });
    if (init?.method === "POST") return new Promise<Response>(() => {});
    return Response.json(repairPayload);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
function showRepair(onClose = vi.fn()) {
  render(<RepertoireIntegrityDialog repertoireId="rep" theme="brown" pieceSet="cburnett"
    onClose={onClose} />);
  return onClose;
}

it("repair hover and selection preview legal arrows and the selected row", async () => {
  fetchFixture(); showRepair();
  const response = await screen.findByRole("button", { name: /^(e4|e2e4)$/ });
  fireEvent.mouseEnter(response.closest("tr")!);
  expect(screen.getByTestId("repair-board").getAttribute("data-shapes"))
    .toContain('"orig":"e2","dest":"e4"');
  fireEvent.click(response);
  expect(response.closest("tr")?.getAttribute("aria-selected")).toBe("true");
});

it("repair save closes after durable enqueue before command receipt or graph completion", async () => {
  fetchFixture(); const onClose = showRepair();
  fireEvent.click(await screen.findByRole("button", { name: /^(e4|e2e4)$/ }));
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  const saved = localStorage.getItem("tempo-pending-integrity-repairs-v2");
  expect(saved).toContain('"selectedMoveUci":"e2e4"');
});

it("repair recommendation is highlighted without selecting or saving a response", async () => {
  const fetcher = fetchFixture(); showRepair();
  await screen.findByText("Suggested response: e4");
  expect(screen.getByRole("button", { name: "Keep this response" }).hasAttribute("disabled")).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/resolve"))).toBe(false);
  expect(localStorage.getItem("tempo-pending-integrity-repairs-v2")).toBeNull();
});

it("repair storage failure retains selection and does not close or report saved", async () => {
  fetchFixture(); const onClose = showRepair();
  fireEvent.click(await screen.findByRole("button", { name: "e4" }));
  const original = Storage.prototype.setItem;
  const failure = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function(this: Storage, key, value) {
    if (key === "tempo-pending-integrity-repairs-v2") throw new Error("Browser storage is full");
    original.call(this, key, value);
  });
  try {
    fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
    expect(onClose).not.toHaveBeenCalled(); expect(screen.getByRole("alert").textContent).toContain("storage is full");
    expect(screen.getByRole("button", { name: "e4" }).closest("tr")?.getAttribute("aria-selected")).toBe("true");
  } finally { failure.mockRestore(); }
});

it("repair navigation previews the chosen continuation and restores the decision", async () => {
  fetchFixture(); showRepair(); await screen.findByText("Suggested response: e4");
  fireEvent.click(screen.getByRole("button", { name: "Use suggested response" }));
  fireEvent.click(screen.getByRole("button", { name: "Next move" }));
  expect(screen.getByTestId("repair-board").getAttribute("data-fen")).toContain("4P3");
  fireEvent.click(screen.getByRole("button", { name: "Decision position" }));
  expect(screen.getByTestId("repair-board").getAttribute("data-fen")).toBe(fen);
});

it("repair preserves a manual response when the recommendation arrives late", async () => {
  let finish: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).includes("/recommendations")) return init?.method === "POST" ? Response.json({ state: "waiting" }) :
      new Promise<Response>(resolve => { finish = resolve; });
    return Response.json(String(input).includes("position-summary") ? { moves: [] } : repairPayload);
  }));
  showRepair(); fireEvent.click(await screen.findByRole("button", { name: "d4" }));
  finish?.(Response.json(preview)); await screen.findByText("Suggested response: e4");
  expect(screen.getByRole("button", { name: "d4" }).closest("tr")?.getAttribute("aria-selected")).toBe("true");
});

it("repair provider failures remain independent and allow an explicit legal response", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("recommendations")) return Response.json({ ...preview, state: "failed", candidates: [],
      suggested_move_uci: null, reason: "Docker Stockfish unavailable; retry analysis" });
    if (url.includes("position-summary")) return Response.json({}, { status: 503 });
    return Response.json(repairPayload);
  }));
  const close = showRepair();
  await screen.findByText("Docker Stockfish unavailable; retry analysis");
  await screen.findByText(/Personal games unavailable/);
  fireEvent.click(screen.getByRole("button", { name: "d4" }));
  fireEvent.click(screen.getByRole("button", { name: "Keep this response" }));
  expect(close).toHaveBeenCalledOnce();
});

it("repair preparation reports a blocked receipt instead of waiting indefinitely for an absent recommendation", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("recommendations") && init?.method === "POST") return Response.json({ operation_id: "prepare-operation", state: "pending" }, { status: 202 });
    if (url.includes("operations")) return Response.json({ state: "blocked", last_error: { message: "Local writer is unavailable; check the service" } });
    return Response.json(url.includes("position-summary") ? { moves: [] } : repairPayload);
  }));
  showRepair();
  await screen.findByText("Local writer is unavailable; check the service");
  fireEvent.click(screen.getByRole("button", { name: "e4" }));
  expect(screen.getByRole("button", { name: "Keep this response" }).hasAttribute("disabled")).toBe(false);
});

it("an empty repair scan in progress does not claim that the repertoire is clean", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ ...repairPayload, status: "unchecked", issues: [],
    issue_count: 0, first_issue_id: null, scan_status: "queued" })));
  showRepair();
  await screen.findByText(/Repertoire validation is still running/);
  expect(screen.queryByText("This repertoire is clean.")).toBeNull();
});
