import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { DiscoveriesTray } from "../../app/components/discoveries-tray";
import { DISCOVERY_ADMISSION_QUEUED, flushPendingDiscoveryAdmissions, pendingDiscoveryAdmissions } from
  "../../app/lib/discovery-admission-outbox";
import { clearDataDiagnostics, dataDiagnostics } from "../../app/lib/validated-data";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { Chess } from "chess.js";
import { clearNotificationHistory, notifications, publishNotification } from "../../app/lib/notifications";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
const backgroundFetch = vi.hoisted(() => vi.fn());
vi.mock("../../app/lib/background-fetch", () => ({
  backgroundFetch: (input: RequestInfo | URL, init?: RequestInit) =>
    String(input).includes("/recommendations")
      ? fetch(input, init)
      : backgroundFetch(input, init),
}));
vi.mock("../../app/components/board/chessboard", async () => {
  const { KeyboardTestBoard } = await import("./keyboard-board-fixture");
  return { Chessboard: (props: React.ComponentProps<typeof KeyboardTestBoard>) => <KeyboardTestBoard {...props} testId="discovery-board" /> };
});
vi.mock("../../app/lib/engine-broker", () => ({ requestInteractiveAnalysis: async () => [] }));
vi.mock("../../app/lib/lichess-explorer", () => ({ loadExplorer: () => new Promise(() => {}) }));

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

beforeEach(() => { localStorage.clear(); clearNotificationHistory(); });

it("discovery confirmation waits for a queued admission", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    opportunityId: "gap", selectedMoveUci: "g1f3", evidenceFingerprint: "revision",
    state: "failed", error: "stale evidence",
  }]));
  const notificationId = publishNotification({ severity: "error", source: "discovery save",
    key: "discovery-save:gap", message: "Discovery save failed: stale evidence" });
  backgroundFetch.mockReset();
  backgroundFetch.mockResolvedValue(Response.json({
    discoveries: [], total: 0, next_offset: null, unread_count: 0,
  }));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  await waitFor(() => expect(backgroundFetch).toHaveBeenCalled());
  expect(notifications().find((record) => record.id === notificationId)).toMatchObject({
    severity: "error", resolvedAt: null,
  });
  act(() => window.dispatchEvent(new CustomEvent(DISCOVERY_ADMISSION_QUEUED,
    { detail: { opportunityId: "gap" } })));
  expect(notifications().find((record) => record.id === notificationId)).toMatchObject({
    severity: "success", message: "Discovery save confirmed.",
  });
});

it("slow discovery pagination does not start overlapping background refreshes", async () => {
  let finishFirstPage: ((response: Response) => void) | undefined;
  let refreshTick: (() => void) | undefined;
  backgroundFetch.mockReset();
  backgroundFetch.mockImplementationOnce(() => new Promise<Response>((resolve) => { finishFirstPage = resolve; }));
  backgroundFetch.mockImplementation(async () => Response.json({
    discoveries: [], total: 0, next_offset: null, unread_count: 0,
  }));
  const originalSetInterval = window.setInterval.bind(window);
  const interval = vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if ((delay ?? 0) < 5_000)
      return originalSetInterval(callback, delay) as unknown as NodeJS.Timeout;
    refreshTick = callback as () => void;
    return originalSetInterval(() => undefined, delay) as unknown as NodeJS.Timeout;
  });
  try {
    render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
    await waitFor(() => expect(backgroundFetch).toHaveBeenCalledTimes(1));
    act(() => { refreshTick?.(); refreshTick?.(); refreshTick?.(); });
    expect(backgroundFetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      finishFirstPage?.(Response.json({ discoveries: [], total: 0, next_offset: null, unread_count: 0 }));
    });
    act(() => { refreshTick?.(); });
    await waitFor(() => expect(backgroundFetch).toHaveBeenCalledTimes(2));
  } finally {
    interval.mockRestore();
  }
});

it("discoveries badge waits for a safe break and does not interrupt twice", async () => {
  const discovery = {
    id: "finding", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.8,
    evidence: { analysis_based: true, window_days: 90, encounter_count: 5,
      version: 2, analyzed_count: 5, miss_count: 3, strong_prefix_count: 4,
      sufficient_prefix_count: 5, immediate_cp_sample_count: 2,
      strong_prefix_rate: 0.8, strong_prefix_qualifies: true, immediate_average_loss_cp: 140,
      later_average_change_cp: null, later_sample_count: 0 },
    evidence_fingerprint: "fingerprint",
    seen_at: null, snoozed_until: null, admission_state: null, admitted_card_id: null,
    unread: true, source_games: [], routes: ["e4 e5 Nf3"],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  };
  backgroundFetch.mockImplementation(async () => Response.json({
    discoveries: [discovery], total: 1, next_offset: null, unread_count: 1,
  }));
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    void input;
    return Response.json({ acknowledged: true });
  });
  vi.stubGlobal("fetch", fetcher);
  const props = { safeToOpen: false, safeBreakCounter: 0,
    onOpenRepertoire: vi.fn(), onQueueChanged: async () => {} };
  const view = render(<DiscoveriesTray {...props} />);
  await waitFor(() => expect(screen.getByLabelText("1 new discoveries")).toBeTruthy());
  expect(screen.queryByRole("dialog", { name: "Discoveries" })).toBeNull();
  view.rerender(<DiscoveriesTray {...props} interactionBlocked safeBreakCounter={1} />);
  expect(screen.queryByRole("dialog", { name: "Discoveries" })).toBeNull();
  view.rerender(<DiscoveriesTray {...props} safeBreakCounter={2} />);
  await waitFor(() => expect(screen.getByText("Strong opening, weak next decision")).toBeTruthy());
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/acknowledge"))).toHaveLength(1));
  fireEvent.click(screen.getByRole("button", { name: "Back to work" }));
  view.rerender(<DiscoveriesTray {...props} safeBreakCounter={3} />);
  expect(screen.queryByText("Strong opening, weak next decision")).toBeNull();
});

it("refreshes stale discovery evidence once and displays the recalculated values", async () => {
  const discovery = {
    id: "stale-finding", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.8,
    evidence: { analysis_based: true, version: 1, encounter_count: 5 },
    evidence_fingerprint: "fingerprint", seen_at: "2026-09-24T00:00:00Z", snoozed_until: null,
    admission_state: null, admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  };
  let evidenceIsFresh = false;
  const evidence = { analysis_based: true, version: 2, window_days: 90, encounter_count: 5,
    analyzed_count: 5, miss_count: 3, strong_prefix_count: 4, sufficient_prefix_count: 5,
    strong_prefix_rate: 0.8, strong_prefix_qualifies: true, immediate_average_loss_cp: 140,
    immediate_cp_sample_count: 2, later_average_change_cp: null, later_sample_count: 0 };
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [
    { ...discovery, evidence: evidenceIsFresh ? evidence : discovery.evidence },
  ], total: 1, next_offset: null, unread_count: 0 }));
  let finishEvidenceRefresh: (() => void) | undefined;
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/opportunities/refresh")) {
      await new Promise<void>((resolve) => { finishEvidenceRefresh = resolve; });
      evidenceIsFresh = true;
    }
    return Response.json({ queued: true });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Refreshing older analysis evidence/)).toBeTruthy());
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/opportunities/refresh"))).toHaveLength(1);
  finishEvidenceRefresh?.();
  await waitFor(() => expect(screen.getByText(/Analysis coverage: 5 of 5 encounters/)).toBeTruthy(), { timeout: 7000 });
  fireEvent.click(screen.getByText("Why this position was flagged"));
  expect(screen.getByText(/Immediate loss: 140 cp over 2 complete samples/)).toBeTruthy();
  expect(screen.getByText(/Observed change through your third later turn: no complete games/)).toBeTruthy();
  expect(screen.queryByText(/unavailable/)).toBeNull();
});

it("describes valid zero-sample analysis without requesting an evidence refresh", async () => {
  const discovery = {
    id: "no-follow-up", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.8,
    evidence: { analysis_based: true, version: 2, window_days: 90, encounter_count: 5,
      analyzed_count: 5, miss_count: 3, strong_prefix_count: 4, sufficient_prefix_count: 5,
      immediate_average_loss_cp: null, immediate_cp_sample_count: 0,
      later_average_change_cp: null, later_sample_count: 0 },
    evidence_fingerprint: "fingerprint", seen_at: "2026-09-24T00:00:00Z", snoozed_until: null,
    admission_state: null, admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  };
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [discovery], total: 1,
    next_offset: null, unread_count: 0 }));
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/opportunities/no-follow-up/training-eligibility"))
      return Response.json({ eligible: true, reason: null });
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  fireEvent.click(await screen.findByText("Why this position was flagged"));
  expect(screen.getByText(/Immediate loss: no complete samples/)).toBeTruthy();
  expect(screen.getByText(/Observed change through your third later turn: no complete games/)).toBeTruthy();
  await waitFor(() => expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
    "http://127.0.0.1:8000/api/repertoires/rep/opportunities/no-follow-up/training-eligibility",
  ]));
});

it("ready discovery queue paginates before navigation and keeps feed order", async () => {
  const base = {
    repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.5,
    evidence: { analysis_based: true, window_days: 90, encounter_count: 5,
      analyzed_count: 5, miss_count: 3 }, evidence_fingerprint: "same",
    seen_at: "2026-09-24T00:00:00Z", snoozed_until: null,
    admission_state: null, admitted_card_id: null, unread: false,
    source_games: [], routes: [], created_at: "2026-09-24T00:00:00Z",
    updated_at: "2026-09-24T00:00:00Z",
  };
  backgroundFetch.mockImplementation(async (url: string) => Response.json(url.includes("offset=100")
    ? { discoveries: [{ ...base, id: "second" }], total: 101, next_offset: null, unread_count: 0 }
    : { discoveries: [{ ...base, id: "first" }], total: 101, next_offset: 100, unread_count: 0 }));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0}
    onOpenRepertoire={vi.fn()} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText("1 of 2 · white to move")).toBeTruthy());
  expect(screen.getAllByText("Recurring weak decision")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByText("2 of 2 · white to move")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Load more discoveries" })).toBeNull();
});

it("shows only ready discoveries in feed order and does not acknowledge hidden items", async () => {
  const makeDiscovery = (id: string, options: { cardId?: string | null; admissionState?: string | null; unread?: boolean } = {}) => ({
    id, repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: options.cardId ?? null, opponent_move_uci: null, trained_color: "white", score: 0.5,
    evidence: { supporting_games: 0 }, evidence_fingerprint: `${id}-fingerprint`,
    seen_at: null, snoozed_until: null, admission_state: options.admissionState ?? null,
    admitted_card_id: null, unread: options.unread ?? false, source_games: [], routes: [id],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  });
  const pageOne = [makeDiscovery("slow-ready"), makeDiscovery("fast-ready"),
    makeDiscovery("waiting", { unread: true }), makeDiscovery("unavailable", { unread: true }),
    makeDiscovery("stale", { unread: true })];
  const pageTwo = [makeDiscovery("failed", { unread: true }), makeDiscovery("saved-card", { cardId: "card" }),
    makeDiscovery("admission-pending", { cardId: "pending-card", admissionState: "preparing" })];
  backgroundFetch.mockImplementation(async (url: string) => Response.json(url.includes("offset=100")
    ? { discoveries: pageTwo, total: 8, next_offset: null, unread_count: 5 }
    : { discoveries: pageOne, total: 8, next_offset: 100, unread_count: 5 }));
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    const match = path.match(/\/api\/discoveries\/([^/]+)\/recommendations/);
    if (match) {
      const id = match[1];
      if (id === "slow-ready") await new Promise((resolve) => window.setTimeout(resolve, 35));
      if (id === "failed") throw new Error("preview service unavailable");
      if (id === "waiting") return Response.json({ state: "waiting", opportunity_id: id, candidates: [] });
      if (id === "unavailable") return Response.json({ state: "unavailable", opportunity_id: id, candidates: [] });
      const fingerprint = id === "stale" ? "older-fingerprint" : `${id}-fingerprint`;
      return Response.json({ state: "ready", opportunity_id: id, evidence_fingerprint: fingerprint,
        starting_fen: startFen, candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null },
          loss_cp: 0, similarity: "no supported similarity", example_line_id: null,
          repertoire_line_count: 0, exact_transposition: false,
          example_line_name: null, preview_moves_uci: ["g1f3"], engine_version: "Stockfish",
          network_version: "net", depth: 14, report_id: "a".repeat(64),
          source_game_id: `coverage:${id}`, source_ply: 2 }],
        engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 }],
        accepted_moves_uci: [] });
    }
    return Response.json({ acknowledged: true });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  expect(await screen.findByText("Preparing review-ready discoveries.")).toBeTruthy();
  await waitFor(() => expect(screen.getByText("1 of 3 · white to move")).toBeTruthy());
  expect(screen.getByText("Example route: slow-ready")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByText("2 of 3 · white to move")).toBeTruthy();
  expect(screen.getByText("Example route: fast-ready")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  expect(screen.getByText("3 of 3 · white to move")).toBeTruthy();
  expect(screen.getByText("Example route: saved-card")).toBeTruthy();
  expect(screen.getAllByRole("status").some((status) =>
    status.textContent?.includes("Lichess: Loading") && status.textContent.includes("Masters: Loading"))).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/acknowledge"))).toBe(false);
  for (const hiddenId of ["waiting", "unavailable", "stale", "failed", "admission-pending"]) {
    expect(screen.queryByText(`Example route: ${hiddenId}`)).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => String(url).includes(`${hiddenId}/acknowledge`))).toBe(false);
  }
});

it("shows an empty ready queue when every cardless recommendation is unavailable", async () => {
  const discovery = {
    id: "unavailable", repertoire_id: "rep", kind: "missing_response", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: null, opponent_move_uci: null, trained_color: "white", score: 0.5,
    evidence: { supporting_games: 0 }, evidence_fingerprint: "unavailable-fingerprint",
    seen_at: null, snoozed_until: null, admission_state: null, admitted_card_id: null,
    unread: true, source_games: [], routes: [], created_at: "2026-09-24T00:00:00Z",
    updated_at: "2026-09-24T00:00:00Z",
  };
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [discovery], total: 1,
    next_offset: null, unread_count: 1 }));
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    void input;
    return Response.json({ state: "unavailable", opportunity_id: "unavailable", candidates: [] });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "No discoveries ready for review" })).toBeTruthy());
  expect(screen.getByText("There are no complete discoveries to review right now.")).toBeTruthy();
  expect(screen.queryByTestId("discovery-board")).toBeNull();
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("/acknowledge"))).toBe(false);
});

it("missing-response viewer shows the learner board after the reply and selects one move", async () => {
  const beforeReply = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
  const afterReply = "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2";
  const discovery = { id: "gap", repertoire_id: "rep", kind: "missing_response", status: "active",
    fen_key: beforeReply.split(" ").slice(0, 4).join(" "), fen: beforeReply,
    decision_fen: afterReply, decision_route_uci: ["e2e4", "c7c5"],
    accepted_moves_uci: [], card_id: null, opponent_move_uci: "c7c5",
    trained_color: "white", score: 1, evidence: { supporting_games: 4 },
    evidence_fingerprint: "gap-revision", seen_at: "2026-09-24T00:00:00Z",
    snoozed_until: null, admission_state: null, admitted_card_id: null,
    unread: false, source_games: [], routes: ["e4 c5"],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" };
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [discovery],
    total: 1, next_offset: null, unread_count: 0 }));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(String(url).includes("recommendations")
    ? { state: "ready", opportunity_id: "gap", evidence_fingerprint: "gap-revision", starting_fen: afterReply,
        candidates: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0,
          similarity: "no supported similarity", example_line_id: null, example_line_name: null,
          repertoire_line_count: 0, exact_transposition: false,
          preview_moves_uci: ["g1f3"], engine_version: "Stockfish", network_version: "net",
          depth: 14, report_id: "a".repeat(64), source_game_id: "coverage:node", source_ply: 2 }],
        engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 0, depth: 14 }],
        accepted_moves_uci: [] }
    : { acknowledged: true })));
  const openBuilder = vi.fn();
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}}
    onOpenBuilder={openBuilder} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterReply));
  await waitFor(() => expect(screen.getByRole("button", { name: "Nf3" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Nf3" }));
  expect(screen.getByTestId("discovery-board").getAttribute("data-shapes")).toContain('"dest":"f3"');
  fireEvent.click(screen.getByRole("button", { name: "Open in Builder" }));
  expect(openBuilder).toHaveBeenCalledWith(expect.objectContaining({ id: "gap", decision_fen: afterReply }), "g1f3");
});

const routeBoard = new Chess(startFen);
routeBoard.move("e4");
const afterE4 = routeBoard.fen();
routeBoard.move("e5");
const afterE5 = routeBoard.fen();
routeBoard.move("Nf3");
const afterNf3 = routeBoard.fen();

function reviewDiscovery(id: string) {
  return { id, repertoire_id: "rep", kind: "missing_response", status: "active",
    fen_key: afterE4.split(" ").slice(0, 4).join(" "), fen: afterE4,
    decision_start_fen: startFen, decision_route_uci: ["e2e4", "e7e5"],
    decision_fen: afterE5, card_id: null, opponent_move_uci: "e7e5",
    trained_color: "white", score: 1, evidence: { supporting_games: 1 },
    evidence_fingerprint: `${id}-revision`, seen_at: "2026-09-24T00:00:00Z",
    snoozed_until: null, admission_state: null, admitted_card_id: null,
    unread: false, source_games: [], routes: ["e4 e5"],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z" };
}

function reviewRecommendation(id: string) {
  return { state: "ready", opportunity_id: id, evidence_fingerprint: `${id}-revision`,
    starting_fen: afterE5, accepted_moves_uci: [], suggested_move_uci: "g1f3",
    suggestion_reason: "same move in a comparable repertoire position",
    candidates: [
      { move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 83,
        similarity: "same move in a comparable repertoire position", example_line_id: "line",
        repertoire_line_count: 3, exact_transposition: false,
        example_line_name: "London", preview_moves_uci: ["g1f3"],
        engine_version: "Stockfish", network_version: "NNUE", depth: 14,
        report_id: "a".repeat(64), source_game_id: "game", source_ply: 2 },
      { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10,
        similarity: "no supported similarity", example_line_id: null,
        repertoire_line_count: 1, exact_transposition: true,
        example_line_name: null, preview_moves_uci: ["d2d4"],
        engine_version: "Stockfish", network_version: "NNUE", depth: 14,
        report_id: "a".repeat(64), source_game_id: "game", source_ply: 2 },
    ],
    engine_lines: [{ move_uci: "g1f3", score: { cp: 20, mate: null }, loss_cp: 83, depth: 14 },
      { move_uci: "d2d4", score: { cp: 10, mate: null }, loss_cp: 10, depth: 14 }],
  };
}

it("pending discovery save is checked before startup preview reads", async () => {
  localStorage.setItem("tempo-pending-discovery-admissions-v1", JSON.stringify([{
    opportunityId: "pending-save", selectedMoveUci: "g1f3",
    evidenceFingerprint: "pending-save-revision", state: "pending",
  }]));
  backgroundFetch.mockImplementation(async () => Response.json({
    discoveries: [reviewDiscovery("preview")], total: 1, next_offset: null, unread_count: 0,
  }));
  let finishSave: ((response: Response) => void) | undefined;
  const fetcher = vi.fn((url: string) => String(url).endsWith("/accept")
    ? new Promise<Response>((resolve) => { finishSave = resolve; })
    : Promise.resolve(Response.json(reviewRecommendation("preview"))));
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  await waitFor(() => expect(backgroundFetch).toHaveBeenCalled());
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(String(fetcher.mock.calls[0][0])).toContain("/accept");
  await act(async () => finishSave?.(Response.json({ status: "preparing", intent_id: "intent" })));
  await waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).includes("/recommendations"))).toBe(true));
});

it("discovery comparison shows repertoire counts, engine tradeoffs, and an eligible alternative", async () => {
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [reviewDiscovery("comparison")],
    total: 1, next_offset: null, unread_count: 0 }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(reviewRecommendation("comparison"))));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  const comparison = await screen.findByRole("region", { name: "Discovery move comparison" });
  expect(comparison.textContent).toContain("3 comparable repertoire lines");
  expect(comparison.textContent).toContain("83 cp from best");
  expect(comparison.textContent).toContain("1 comparable repertoire line");
  expect(comparison.textContent).toContain("3 versus 1 comparable repertoire lines");
  expect(comparison.textContent).toContain("83 versus 10 cp from best");
  expect(comparison.textContent).toContain("Exact transposition");
  expect(comparison.textContent).toContain("100 cp from best");
  expect(comparison.textContent).toContain("30 cp");
  fireEvent.click(screen.getByRole("button", { name: "Choose d4" }));
  expect(screen.getByRole("button", { name: "Choose d4" }).getAttribute("aria-pressed")).toBe("true");
});

it("discovery comparison keeps mate evaluations separate from centipawn gaps", async () => {
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [reviewDiscovery("mate")],
    total: 1, next_offset: null, unread_count: 0 }));
  const baseRecommendation = reviewRecommendation("mate");
  const recommendation = { ...baseRecommendation, candidates: baseRecommendation.candidates.map((candidate, index) =>
    index === 0 ? { ...candidate, score: { cp: null, mate: 3 }, loss_cp: null } : candidate) };
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(recommendation)));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  const comparison = await screen.findByRole("region", { name: "Discovery move comparison" });
  expect(comparison.textContent).toContain("Mate line; no cp gap");
});

it("discovery board keys inspect route and preview while the familiar default stays selected", async () => {
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [reviewDiscovery("route")],
    total: 1, next_offset: null, unread_count: 0 }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(reviewRecommendation("route"))));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Suggested Nf3/)).toBeTruthy());
  expect(screen.getByRole("button", { name: "Add and train" }).hasAttribute("disabled")).toBe(false);
  expect(screen.getByTestId("discovery-board").getAttribute("data-shapes")).toContain('"brush":"blue"');
  expect(screen.getByTestId("discovery-board").getAttribute("data-shapes")).toContain('"brush":"green"');
  fireEvent.keyDown(window, { key: "ArrowLeft" });
  await waitFor(() => expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterE4));
  expect(screen.getByTestId("discovery-board").getAttribute("data-last-move")).toContain("e2");
  fireEvent.click(screen.getByRole("button", { name: "Next move" }));
  expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterE5);
  fireEvent.keyDown(window, { key: "ArrowRight" });
  expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterNf3);
  fireEvent.click(screen.getByRole("button", { name: "d4" }));
  expect(screen.getByRole("button", { name: "Suggested Nf3" }).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterE5);
});

it("invalid discovery route falls back to the decision board", async () => {
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [
    { ...reviewDiscovery("invalid-route"), decision_route_uci: ["e2e5"] },
  ], total: 1, next_offset: null, unread_count: 0 }));
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(reviewRecommendation("invalid-route"))));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Suggested Nf3/)).toBeTruthy());
  expect(screen.getByTestId("discovery-board").getAttribute("data-fen")).toBe(afterE5);
  expect(screen.getByRole("button", { name: "Previous move" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByTestId("discovery-board").getAttribute("data-shapes")).toContain('"brush":"green"');
});

it("Add and train advances before saving and preserves the review order after refresh", async () => {
  let feedOrder = [reviewDiscovery("first"), reviewDiscovery("second")];
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: feedOrder,
    total: 2, next_offset: null, unread_count: 0 }));
  let finishSave: ((response: Response) => void) | undefined;
  vi.stubGlobal("fetch", vi.fn((url: string) => {
    if (url.includes("/recommendations"))
      return Promise.resolve(Response.json(reviewRecommendation(url.includes("first") ? "first" : "second")));
    if (url.includes("/accept"))
      return new Promise<Response>((resolve) => { finishSave = resolve; });
    if (url.includes("/api/discovery-admissions/"))
      return Promise.resolve(Response.json({ state: "queued", error: null }));
    return Promise.resolve(Response.json({ acknowledged: true }));
  }));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Suggested Nf3/)).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Add and train" }));
  expect(screen.getByText("2 of 2 · white to move")).toBeTruthy();
  expect(pendingDiscoveryAdmissions()).toHaveLength(1);
  feedOrder = [...feedOrder].reverse();
  await act(async () => finishSave?.(Response.json({ status: "preparing", intent_id: "intent" })));
  await act(async () => { await flushPendingDiscoveryAdmissions(); });
  await waitFor(() => expect(backgroundFetch.mock.calls.length).toBeGreaterThan(1));
  fireEvent.click(screen.getByRole("button", { name: "Previous" }));
  expect(screen.getByText("1 of 2 · white to move")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add and train" }).hasAttribute("disabled")).toBe(true);
});

it("confirmed failed discovery save remains visible with a retry action", async () => {
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [reviewDiscovery("failure")],
    total: 1, next_offset: null, unread_count: 0 }));
  vi.stubGlobal("fetch", vi.fn(async (url: string) => url.includes("/recommendations")
    ? Response.json(reviewRecommendation("failure"))
    : Response.json({ detail: "stale evidence" }, { status: 409 })));
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Suggested Nf3/)).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Add and train" }));
  await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("stale evidence"));
  expect(screen.getByRole("button", { name: "Retry save" })).toBeTruthy();
});


function savedEligibilityDiscovery() {
  return {
    id: "eligibility-recovery", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.8,
    evidence: { supporting_games: 0 }, evidence_fingerprint: "unchanged-evidence",
    seen_at: "2026-09-24T00:00:00Z", snoozed_until: null,
    admission_state: null, admitted_card_id: null, unread: false, source_games: [], routes: [],
    created_at: "2026-09-24T00:00:00Z", updated_at: "2026-09-24T00:00:00Z",
  };
}

it("negative discovery eligibility recovers through an explicit recheck after an unchanged feed refresh", async () => {
  const discovery = savedEligibilityDiscovery();
  backgroundFetch.mockReset();
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: [discovery], total: 1,
    next_offset: null, unread_count: 0 }));
  let backendEligible = false;
  let finishRecheck: ((response: Response) => void) | undefined;
  let eligibilityRequests = 0;
  const requests: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    requests.push(url);
    if (url.endsWith("/training-eligibility")) {
      eligibilityRequests += 1;
      if (eligibilityRequests === 2)
        return new Promise<Response>((resolve) => { finishRecheck = resolve; });
      return Response.json(backendEligible
        ? { eligible: true, reason: null }
        : { eligible: false, reason: "The saved card is unavailable or awaiting validation" });
    }
    if (url.endsWith("/train")) return Response.json({ card_id: "card", queued: true, idempotent: false });
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  let refreshTick: (() => void) | undefined;
  const originalSetInterval = window.setInterval.bind(window);
  const interval = vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 30_000) {
      refreshTick = callback as () => void;
      return originalSetInterval(() => undefined, delay) as unknown as NodeJS.Timeout;
    }
    return originalSetInterval(callback, delay) as unknown as NodeJS.Timeout;
  });
  const onQueueChanged = vi.fn(async () => {});
  try {
    render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={onQueueChanged} />);
    fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
    await screen.findByText(/The saved card is unavailable or awaiting validation/);
    const train = screen.getByRole("button", { name: "Train this decision" }) as HTMLButtonElement;
    expect(train.disabled).toBe(true);
    expect(eligibilityRequests).toBe(1);
    backendEligible = true;
    await act(async () => { refreshTick?.(); });
    expect(backgroundFetch).toHaveBeenCalledTimes(2);
    expect(eligibilityRequests).toBe(1);
    expect(train.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Recheck eligibility" }));
    await waitFor(() => expect(eligibilityRequests).toBe(2));
    expect(train.disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Recheck eligibility" })).toBeNull();
    await act(async () => { finishRecheck?.(Response.json({ eligible: true, reason: null })); });
    await waitFor(() => expect(train.disabled).toBe(false));
    fireEvent.click(train);
    await waitFor(() => expect(onQueueChanged).toHaveBeenCalledTimes(1));
    expect(requests.map((url) => url.split("/").at(-1))).toEqual([
      "training-eligibility", "training-eligibility", "training-eligibility", "train",
    ]);
  } finally { interval.mockRestore(); }
});

it("failed negative eligibility recheck remains observable and retries before enabling training", async () => {
  const discovery = savedEligibilityDiscovery();
  backgroundFetch.mockReset();
  backgroundFetch.mockResolvedValue(Response.json({ discoveries: [discovery], total: 1,
    next_offset: null, unread_count: 0 }));
  let finishRetry: ((response: Response) => void) | undefined;
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ eligible: false,
      reason: "The saved card is unavailable or awaiting validation" }))
    .mockResolvedValueOnce(Response.json({ detail: "Eligibility temporarily unavailable" }, { status: 503 }))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishRetry = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  fireEvent.click(await screen.findByRole("button", { name: "Recheck eligibility" }));
  const retry = await screen.findByRole("button", { name: "Retry eligibility" });
  expect(screen.getAllByText(/Eligibility temporarily unavailable/).length).toBeGreaterThan(0);
  const train = screen.getByRole("button", { name: "Train this decision" }) as HTMLButtonElement;
  expect(train.disabled).toBe(true);
  fireEvent.click(retry);
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  expect(screen.queryByText(/Eligibility temporarily unavailable/)).toBeNull();
  expect(train.disabled).toBe(true);
  await act(async () => { finishRetry?.(Response.json({ eligible: true, reason: null })); });
  await waitFor(() => expect(train.disabled).toBe(false));
});


function deferredEligibility<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: Error) => void;
  const promise = new Promise<T>((finish, fail) => { resolve = finish; reject = fail; });
  return { promise, resolve, reject };
}

async function eligibilityLifecycleFixture() {
  const discovery = savedEligibilityDiscovery();
  let feed = [discovery];
  let refreshTick: (() => void) | undefined;
  const requests: ReturnType<typeof deferredEligibility<Response>>[] = [];
  const originalSetInterval = window.setInterval.bind(window);
  const interval = vi.spyOn(window, "setInterval").mockImplementation((callback, delay) => {
    if (delay === 30_000) {
      refreshTick = callback as () => void;
      return originalSetInterval(() => undefined, delay) as unknown as NodeJS.Timeout;
    }
    return originalSetInterval(callback, delay) as unknown as NodeJS.Timeout;
  });
  backgroundFetch.mockReset();
  backgroundFetch.mockImplementation(async () => Response.json({ discoveries: feed, total: feed.length,
    next_offset: null, unread_count: 0 }));
  const fetcher = vi.fn((input: RequestInfo | URL) => {
    if (!String(input).endsWith("/training-eligibility"))
      return Promise.resolve(Response.json({ card_id: "card", queued: true, idempotent: false }));
    const request = deferredEligibility<Response>();
    requests.push(request);
    return request.promise;
  });
  vi.stubGlobal("fetch", fetcher);
  clearDataDiagnostics();
  clearDebugErrors();
  const view = render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  const refreshFeed = async (next: typeof feed) => {
    feed = next;
    await act(async () => { refreshTick?.(); });
  };
  return { discovery, requests, fetcher, refreshFeed,
    train: () => screen.getByRole("button", { name: "Train this decision" }) as HTMLButtonElement,
    dispose: async () => {
      view.unmount();
      await act(async () => { requests.forEach((request) => request.resolve(Response.json({ eligible: true, reason: null }))); });
      interval.mockRestore();
    },
  };
}

const obsoleteEligibilityOutcomes = [
  { name: "positive", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.resolve(Response.json({ eligible: true, reason: null })) },
  { name: "negative", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.resolve(Response.json({ eligible: false, reason: "Obsolete negative eligibility" })) },
  { name: "HTTP failure", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.resolve(Response.json({ detail: "Obsolete eligibility HTTP failure" }, { status: 503 })) },
  { name: "malformed JSON", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.resolve(new Response("{broken")) },
  { name: "malformed schema", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.resolve(Response.json({ eligible: "invalid" })) },
  { name: "network rejection", settle: (request: ReturnType<typeof deferredEligibility<Response>>) =>
    request.reject(new Error("Obsolete eligibility network failure")) },
];

function expectNoObsoleteEligibilityFailure() {
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry eligibility" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Recheck eligibility" })).toBeNull();
  expect(dataDiagnostics()).toHaveLength(0);
  expect(debugErrors()).toHaveLength(0);
}

it.each(obsoleteEligibilityOutcomes)("returned discovery starts fresh eligibility before obsolete request settles: $name", async ({ settle }) => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await fixture.refreshFeed([]);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
    expect(fixture.train().disabled).toBe(true);
    await act(async () => { settle(fixture.requests[0]); });
    expect(fixture.train().disabled).toBe(true);
    expectNoObsoleteEligibilityFailure();
    await act(async () => fixture.requests[1].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
  } finally { await fixture.dispose(); }
});

it.each(obsoleteEligibilityOutcomes)("obsolete eligibility settlement cannot overwrite replacement success: $name", async ({ settle }) => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await fixture.refreshFeed([]);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
    await act(async () => fixture.requests[1].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
    await act(async () => { settle(fixture.requests[0]); });
    expect(fixture.train().disabled).toBe(false);
    expectNoObsoleteEligibilityFailure();
  } finally { await fixture.dispose(); }
});

it("obsolete eligibility cleanup preserves replacement request ownership", async () => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(1);
    await fixture.refreshFeed([]);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
    await act(async () => fixture.requests[0].resolve(Response.json({ eligible: true, reason: null })));
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
    expect(fixture.train().disabled).toBe(true);
    await act(async () => fixture.requests[1].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
  } finally { await fixture.dispose(); }
});

it.each(["evidence_fingerprint", "card_id"] as const)("eligibility identity change away and back invalidates earlier requests: %s", async (field) => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await fixture.refreshFeed([{ ...fixture.discovery, [field]: "replacement" }]);
    expect(fixture.requests).toHaveLength(2);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(3);
    await act(async () => {
      fixture.requests[0].resolve(Response.json({ eligible: true, reason: null }));
      fixture.requests[1].resolve(Response.json({ detail: "Obsolete identity" }, { status: 503 }));
    });
    expect(fixture.train().disabled).toBe(true);
    expectNoObsoleteEligibilityFailure();
    await act(async () => fixture.requests[2].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
  } finally { await fixture.dispose(); }
});

it.each([
  { name: "schema failure", status: 200, raw: { eligible: "invalid" } },
  { name: "HTTP failure", status: 503, raw: { detail: "Obsolete decoded HTTP failure" } },
])("obsolete eligibility body decoding cannot publish validation diagnostics: $name", async ({ status, raw }) => {
  const fixture = await eligibilityLifecycleFixture();
  const body = deferredEligibility<unknown>();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    const response = Response.json({}, { status });
    const decode = vi.spyOn(response, "json").mockReturnValue(body.promise);
    await act(async () => fixture.requests[0].resolve(response));
    expect(decode).toHaveBeenCalledTimes(1);
    await fixture.refreshFeed([]);
    // Finish decoding while absent: even an identity-only handler rejects this response,
    // but reporting inside the shared reader must also be guarded.
    await act(async () => body.resolve(raw));
    expect(dataDiagnostics()).toHaveLength(0);
    expect(debugErrors()).toHaveLength(0);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(2);
    expect(fixture.train().disabled).toBe(true);
    await act(async () => fixture.requests[1].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
  } finally { body.resolve({ eligible: true, reason: null }); await fixture.dispose(); }
});

it.each([obsoleteEligibilityOutcomes[0], obsoleteEligibilityOutcomes[2], obsoleteEligibilityOutcomes[5]])(
  "obsolete command-time eligibility cannot cache authorization or submit Train: $name", async ({ settle }) => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await act(async () => fixture.requests[0].resolve(Response.json({ eligible: true, reason: null })));
    fireEvent.click(fixture.train());
    expect(fixture.requests).toHaveLength(2);
    await fixture.refreshFeed([]);
    await fixture.refreshFeed([fixture.discovery]);
    expect(fixture.requests).toHaveLength(3);
    await act(async () => { settle(fixture.requests[1]); });
    expectNoObsoleteEligibilityFailure();
    expect(fixture.fetcher.mock.calls.every(([input]) => String(input).endsWith("/training-eligibility"))).toBe(true);
    expect(fixture.train().disabled).toBe(true);
    await act(async () => fixture.requests[2].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
  } finally { await fixture.dispose(); }
});


it.each(obsoleteEligibilityOutcomes.slice(2))("current eligibility failure remains observable and retryable: $name", async ({ name, settle }) => {
  const fixture = await eligibilityLifecycleFixture();
  try {
    await waitFor(() => expect(fixture.requests).toHaveLength(1));
    await act(async () => { settle(fixture.requests[0]); });
    expect(fixture.train().disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Retry eligibility" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBeTruthy();
    if (name === "HTTP failure" || name === "malformed schema") expect(debugErrors()).toHaveLength(1);
    if (name === "malformed schema") expect(dataDiagnostics()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry eligibility" }));
    expect(fixture.requests).toHaveLength(2);
    expect(fixture.train().disabled).toBe(true);
    await act(async () => fixture.requests[1].resolve(Response.json({ eligible: true, reason: null })));
    expect(fixture.train().disabled).toBe(false);
  } finally { await fixture.dispose(); }
});
