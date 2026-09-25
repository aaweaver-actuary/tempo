import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DiscoveriesTray } from "../../app/components/discoveries-tray";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
const backgroundFetch = vi.hoisted(() => vi.fn());
vi.mock("../../app/lib/background-fetch", () => ({ backgroundFetch: (...args: unknown[]) => backgroundFetch(...args) }));
vi.mock("../../app/components/chessboard", () => ({
  Chessboard: ({ fen, shapes }: { fen: string; shapes: unknown[] }) =>
    <div data-testid="discovery-board" data-fen={fen} data-shapes={JSON.stringify(shapes)} />,
}));
vi.mock("../../app/lib/engine-broker", () => ({ requestInteractiveAnalysis: async () => [] }));
vi.mock("../../app/lib/lichess-explorer", () => ({ loadExplorer: () => new Promise(() => {}) }));

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

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
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/opportunities/refresh")) evidenceIsFresh = true;
    return Response.json({ queued: true });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  await waitFor(() => expect(screen.getByText(/Refreshing older analysis evidence/)).toBeTruthy());
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/opportunities/refresh"))).toHaveLength(1);
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
  const fetcher = vi.fn(async () => Response.json({ acknowledged: true }));
  vi.stubGlobal("fetch", fetcher);
  render(<DiscoveriesTray safeToOpen={false} safeBreakCounter={0} onQueueChanged={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Discoveries" }));
  fireEvent.click(await screen.findByText("Why this position was flagged"));
  expect(screen.getByText(/Immediate loss: no complete samples/)).toBeTruthy();
  expect(screen.getByText(/Observed change through your third later turn: no complete games/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();
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
