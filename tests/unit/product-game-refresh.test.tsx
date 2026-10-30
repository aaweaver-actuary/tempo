import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { GameReviewView } from "../../app/features/game-review/GameReviewView";
import { useGameRefresh } from "../../app/features/game-review/use-game-refresh";
import type { GameReviewClient } from "../../app/features/game-review/api";
import { GameDetailSchema, RecentGamesSchema, type GameRefreshStatus } from "../../app/features/product-api/contracts";
import { storePendingGameRefresh, readPendingGameRefresh } from "../../database/browser/adapters/games-analysis/game-sync";
import fixture from "../fixtures/game-review-current-api.json";
import { GameReviewRequestError } from "../../app/features/game-review/api";

function status(provider: "lichess" | "chesscom", operation_id: string,
  state: GameRefreshStatus["status"] = "queued"): GameRefreshStatus {
  return { provider, operation_id, job_id: `job:${operation_id}`, status: state,
    window: { start_at: "2026-10-25T00:00:00Z", end_at: "2026-10-30T12:00:00Z", precision: "millisecond" },
    pages_committed: state === "complete" ? 2 : 0, next_attempt_at: 0, error: null };
}
function client(overrides: Partial<GameReviewClient> = {}): GameReviewClient {
  return { refreshScope: "http://games.test/api/product/game-review/refresh:legacy",
    recentGames: vi.fn(async () => RecentGamesSchema.parse(fixture.recent)),
    gameDetail: vi.fn(async () => GameDetailSchema.parse(fixture.detail)),
    sync: vi.fn(), retryFeedback: vi.fn(),
    refresh: vi.fn(async ({ provider, operation_id }) => status(provider, operation_id)),
    refreshStatus: vi.fn(async ({ operation_id }) => status("lichess", operation_id)), ...overrides };
}

it("polls the exact refresh job and updates games automatically without moving the selected board", async () => {
  let finished = false;
  const api = client({ refreshStatus: vi.fn(async ({ operation_id }) => {
    finished = true; return status("lichess", operation_id, "complete");
  }), recentGames: vi.fn(async () => RecentGamesSchema.parse({ ...fixture.recent,
    games: finished ? [...fixture.recent.games, { ...fixture.recent.games[0], game_id: "fresh-game" }] : fixture.recent.games })) });
  render(<GameReviewView api={api} />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`Lichess · ${fixture.detail.game_id}`) }));
  await screen.findByRole("button", { name: /^Next$/ });
  fireEvent.click(screen.getByRole("button", { name: /^Next$/ }));
  const ply = screen.getByText(/^Ply \d+ of/).textContent;
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "Refresh Lichess now" }));
  await act(async () => {});
  expect(api.refresh).toHaveBeenCalledTimes(1);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(api.refreshStatus).toHaveBeenCalledTimes(1);
  const original = vi.mocked(api.refresh).mock.calls[0][0].operation_id;
  expect(vi.mocked(api.refreshStatus).mock.calls[0][0].operation_id).toBe(original);
  expect(screen.getByRole("button", { name: /Lichess · fresh-game/ })).toBeTruthy();
  expect(screen.getByText(/^Ply \d+ of/).textContent).toBe(ply);
  expect(api.gameDetail).toHaveBeenCalledTimes(1);
  expect(readPendingGameRefresh(api.refreshScope!, "lichess")).toBeNull();
});

it("recovers an accepted refresh after unmount through its exact job without another POST", async () => {
  const api = client();
  const first = renderHook(() => useGameRefresh(api, vi.fn()));
  act(() => first.result.current.refresh("lichess"));
  await waitFor(() => expect(first.result.current.progress.lichess.job?.status).toBe("queued"));
  const original = vi.mocked(api.refresh).mock.calls[0][0].operation_id;
  first.unmount();
  const second = renderHook(() => useGameRefresh(api, vi.fn()));
  await waitFor(() => expect(second.result.current.progress.lichess.job?.operation_id).toBe(original));
  expect(api.refresh).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.refreshStatus).mock.calls[0][0].operation_id).toBe(original);
});

it("keeps the two providers independent when one refresh fails", async () => {
  const api = client({ refresh: vi.fn(async ({ provider, operation_id }) => provider === "lichess"
    ? { ...status(provider, operation_id, "failed"), error: "Account not found" }
    : status(provider, operation_id)) });
  const view = renderHook(() => useGameRefresh(api, vi.fn()));
  act(() => { view.result.current.refresh("lichess"); view.result.current.refresh("chesscom"); });
  await waitFor(() => expect(view.result.current.progress.lichess.job?.status).toBe("failed"));
  expect(view.result.current.progress.chesscom.pending).toBe(true);
  expect(view.result.current.progress.chesscom.job?.provider).toBe("chesscom");
  expect(readPendingGameRefresh(api.refreshScope!, "lichess")).toBeNull();
  expect(readPendingGameRefresh(api.refreshScope!, "chesscom")).not.toBeNull();
});

it("stops hidden polling and checks immediately when Games becomes visible", async () => {
  let visibility = "visible";
  const spy = vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility as DocumentVisibilityState);
  const api = client();
  const view = renderHook(() => useGameRefresh(api, vi.fn()));
  act(() => view.result.current.refresh("lichess"));
  await waitFor(() => expect(view.result.current.progress.lichess.job).toBeTruthy());
  vi.useFakeTimers();
  act(() => { visibility = "hidden"; document.dispatchEvent(new Event("visibilitychange")); });
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(api.refreshStatus).not.toHaveBeenCalled();
  await act(async () => { visibility = "visible"; document.dispatchEvent(new Event("visibilitychange")); });
  expect(api.refreshStatus).toHaveBeenCalledTimes(1);
  spy.mockRestore();
});

it("ignores a late completed status after unmount and retains its recovery identity", async () => {
  let resolve!: (value: GameRefreshStatus) => void;
  const pending = new Promise<GameRefreshStatus>(done => { resolve = done; });
  const completed = vi.fn();
  const api = client({ refreshStatus: vi.fn(() => pending) });
  storePendingGameRefresh(api.refreshScope!, "lichess", { provider: "lichess", operation_id: "original", job_id: "job:original" });
  const view = renderHook(() => useGameRefresh(api, completed));
  await waitFor(() => expect(api.refreshStatus).toHaveBeenCalledTimes(1));
  view.unmount();
  await act(async () => resolve(status("lichess", "original", "complete")));
  expect(completed).not.toHaveBeenCalled();
  expect(readPendingGameRefresh(api.refreshScope!, "lichess")).not.toBeNull();
});

it("releases a definitively rejected configuration request so a corrected refresh can start", async () => {
  const api = client({ refresh: vi.fn().mockRejectedValueOnce(new GameReviewRequestError("Save Games Settings first", 422))
    .mockImplementation(async ({ provider, operation_id }) => status(provider, operation_id)) });
  const view = renderHook(() => useGameRefresh(api, vi.fn()));
  act(() => view.result.current.refresh("lichess"));
  await waitFor(() => expect(view.result.current.progress.lichess.error).toBe("Save Games Settings first"));
  expect(view.result.current.progress.lichess.pending).toBe(false);
  expect(readPendingGameRefresh(api.refreshScope!, "lichess")).toBeNull();
  act(() => view.result.current.refresh("lichess"));
  await waitFor(() => expect(view.result.current.progress.lichess.job?.status).toBe("queued"));
  expect(vi.mocked(api.refresh).mock.calls[0][0].operation_id).not.toBe(vi.mocked(api.refresh).mock.calls[1][0].operation_id);
});

it("does not clear a newer tab's request when an earlier job completes late", async () => {
  let resolve!: (value: GameRefreshStatus) => void;
  const pending = new Promise<GameRefreshStatus>(done => { resolve = done; });
  const api = client({ refreshStatus: vi.fn(() => pending) });
  storePendingGameRefresh(api.refreshScope!, "lichess", { provider: "lichess", operation_id: "earlier", job_id: "job:earlier" });
  const view = renderHook(() => useGameRefresh(api, vi.fn()));
  await waitFor(() => expect(api.refreshStatus).toHaveBeenCalledTimes(1));
  storePendingGameRefresh(api.refreshScope!, "lichess", { provider: "lichess", operation_id: "newer", job_id: "job:newer" });
  await act(async () => resolve(status("lichess", "earlier", "complete")));
  expect(JSON.parse(readPendingGameRefresh(api.refreshScope!, "lichess")!).operation_id).toBe("newer");
  expect(view.result.current.progress.lichess.pending).toBe(true);
});
