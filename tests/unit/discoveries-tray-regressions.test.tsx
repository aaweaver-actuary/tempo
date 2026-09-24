import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DiscoveriesTray } from "../../app/components/discoveries-tray";

vi.mock("../../app/utils/local", () => ({ usesLocalApi: () => true }));
const backgroundFetch = vi.hoisted(() => vi.fn());
vi.mock("../../app/lib/background-fetch", () => ({ backgroundFetch: (...args: unknown[]) => backgroundFetch(...args) }));

const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

it("discoveries badge waits for a safe break and does not interrupt twice", async () => {
  const discovery = {
    id: "finding", repertoire_id: "rep", kind: "weak_known_decision", status: "active",
    fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
    card_id: "card", opponent_move_uci: null, trained_color: "white", score: 0.8,
    evidence: { analysis_based: true, window_days: 90, encounter_count: 5,
      analyzed_count: 5, miss_count: 3, strong_prefix_count: 4,
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
  const fetcher = vi.fn(async () => Response.json({ acknowledged: true }));
  vi.stubGlobal("fetch", fetcher);
  const props = { safeToOpen: false, safeBreakCounter: 0,
    onOpenRepertoire: vi.fn(), onQueueChanged: async () => {} };
  const view = render(<DiscoveriesTray {...props} />);
  await waitFor(() => expect(screen.getByLabelText("1 new discoveries")).toBeTruthy());
  expect(screen.queryByRole("region", { name: "Discoveries" })).toBeNull();
  view.rerender(<DiscoveriesTray {...props} interactionBlocked safeBreakCounter={1} />);
  expect(screen.queryByRole("region", { name: "Discoveries" })).toBeNull();
  view.rerender(<DiscoveriesTray {...props} safeBreakCounter={2} />);
  await waitFor(() => expect(screen.getByText("Strong opening, weak next decision")).toBeTruthy());
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  view.rerender(<DiscoveriesTray {...props} safeBreakCounter={3} />);
  expect(screen.queryByText("Strong opening, weak next decision")).toBeNull();
});

it("discoveries tray loads later pages without losing the first page", async () => {
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
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more discoveries" })).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: "Load more discoveries" }));
  await waitFor(() => expect(screen.getAllByText("Recurring weak decision")).toHaveLength(2));
  expect(screen.queryByRole("button", { name: "Load more discoveries" })).toBeNull();
});
