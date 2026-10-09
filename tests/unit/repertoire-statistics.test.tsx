import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RepertoireStatistics } from "../../app/components/repertoire-statistics";

vi.mock("../../app/components/chessboard", () => ({ Chessboard: ({ fen }: { fen: string }) => <div data-testid="repertoire-board">{fen}</div> }));
const fixture = (url: string) => url.includes("/positions") ? {
    positions: [{ fen_key: "start w KQkq -", fen: "start w KQkq - 0 1", games: 4, encounters: 4, correct: 2,
      missed: 2, last_seen_at: "2026-09-25T12:00:00Z", expected_moves: ["e2e4"],
      played_moves: [{ move_uci: "e2e4", count: 2 }, { move_uci: "d2d4", count: 2 }],
      card_id: "card", sample_game_id: "game", sample_ply: 0 }], total: 1, next_cursor: null,
  } : {
    window: "90d", graph_updated_at: "2026-09-25T12:00:00Z", graph_state: "ready", game_state: "ready",
    prefix: { total: 3, active: 2, studied: 1, unseen: 2, locked: 1, paused: 0 },
    cards: { total: 5, new: 1, learning: 2, mature: 1, locked: 1, difficult: 1, due_today: 1, due_next_seven_days: 2 },
    study: { correct: 4, attempts: 5, accuracy: 0.8 },
    games: { matched: 4, correct: 2, decisions: 4, adherence: 0.5, wins: 2, draws: 1, losses: 1, positions_seen: 1, positions_total: 3 },
    unlocks: [{ card_id: "child", parent_card_id: "parent", line_name: "Main line", parent_due_date: "2026-09-27", earliest_unlock_date: "2026-09-28", status: "ready" }],
  };

beforeEach(() => vi.stubGlobal("fetch", vi.fn(async (url: string) => Response.json(fixture(url)))));
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("repertoire statistics separates study accuracy and game adherence and opens supporting games", async () => {
  const onShowGamesAtPosition = vi.fn();
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={onShowGamesAtPosition} />);
  await waitFor(() => expect(screen.getByText("80.0%")).toBeTruthy());
  expect(screen.getByText("50.0%")).toBeTruthy();
  expect(screen.getByText(/1 studied · 2 unseen/)).toBeTruthy();
  expect(screen.getByText(/within your daily new-card limit/)).toBeTruthy();
  expect(screen.getByText(/Ready for introduction/)).toBeTruthy();
  expect(screen.getByTestId("repertoire-board")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "View supporting games" }));
  expect(onShowGamesAtPosition).toHaveBeenCalledWith("start w KQkq - 0 1", "rep");
});

it("repertoire statistics shows an actionable failure instead of zero values", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error("SQLite unavailable"));
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  expect(screen.getByText(/SQLite unavailable/)).toBeTruthy();
  expect(screen.queryByText("80.0%")).toBeNull();
});

it("repertoire statistics labels stale game comparisons while retaining published card counts", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => Response.json(
    String(input).includes("/positions") ? fixture(String(input)) : { ...fixture(String(input)), game_state: "refreshing" },
  ));
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Game comparisons refreshing/)).toBeTruthy());
  expect(screen.getByText(/1 studied · 2 unseen/)).toBeTruthy();
});

it("repertoire statistics hides empty counts until the first graph is published", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => Response.json(
    String(input).includes("/positions") ? fixture(String(input)) : { ...fixture(String(input)), graph_updated_at: null, graph_state: "refreshing" },
  ));
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Preparing the first published repertoire graph/)).toBeTruthy());
  expect(screen.getByText("80.0%").closest("[hidden]")).not.toBeNull();
});

it("repertoire statistics directs a failed comparison task to service status", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => Response.json(
    String(input).includes("/positions") ? fixture(String(input)) : { ...fixture(String(input)), game_state: "failed" },
  ));
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Game comparisons failed/)).toBeTruthy());
  expect(screen.getByText(/Retry the failed task in Settings/)).toBeTruthy();
});


it("opening progression statistics explain waiting practice and paused repairs without maturity dates", async () => {
  vi.mocked(fetch).mockImplementation(async (input) => Response.json(
    String(input).includes("/positions") ? fixture(String(input)) : { ...fixture(String(input)), unlocks: [
      { card_id: "child", parent_card_id: "parent", line_name: "Main line", parent_due_date: "2026-09-27", earliest_unlock_date: null, status: "waiting_practice" },
      { card_id: "paused", parent_card_id: "repair", line_name: "Side line", parent_due_date: "2026-09-27", earliest_unlock_date: null, status: "paused" },
    ] },
  ));
  render(<RepertoireStatistics repertoireId="rep" repertoireName="Main" theme="brown" pieceSet="cburnett"
    onBack={vi.fn()} onShowGamesAtPosition={vi.fn()} />);
  await waitFor(() => expect(screen.getByText(/Waiting for parent practice/)).toBeTruthy());
  expect(screen.getByText(/Paused for repair or validation/)).toBeTruthy();
  expect(screen.queryByText(/Earliest dates assume/)).toBeNull();
});
