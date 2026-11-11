import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import LegacyHome from "../../app/views/legacy-home";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { demoCards } from "../../app/samples";
const game = { id: "source-game", provider: "lichess", played_at: "2026-10-20T12:00:00Z", result: "1-0", color: "white" };
const detailPath = `/api/product/game-review/games/${game.id}`;
const detail = {
  color: game.color,
  positions: ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"],
  moves: ["d2d4"],
  comparison: { kind: "departure", ply: 1, played_uci: "d2d4", expected_uci: ["e2e4"] },
};

vi.mock("../../app/lib/study-connection", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/lib/study-connection")>(), independentStudy: false,
}));
vi.mock("../../app/views/fetchAndInitializeQueue", async importOriginal => ({
  ...await importOriginal<typeof import("../../app/views/fetchAndInitializeQueue")>(), fetchAndInitializeQueue: vi.fn(),
}));
vi.mock("../../app/components/board/chessboard", () => ({ Chessboard: () => <div /> }));
vi.mock("../../app/lib/move-sound", () => ({ playMoveSound: vi.fn(), playChessMoveSound: vi.fn(),
  moveSoundEnabled: () => false, prepareMoveSounds: vi.fn(), cancelMoveSounds: vi.fn() }));
// Unchanged authoring/status panels are outside this selected navigation seam.
vi.mock("../../app/components/integrity-repair-status", () => ({ IntegrityRepairStatus: () => null }));

it("mounts minimal saved Game Review and keeps the Study attempt when its optional read fails", async () => {
  let unavailable = false;
  const productRequests: string[] = [];
  const requests: { path: string; method: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://fixture");
    const path = url.pathname;
    requests.push({ path, method: init?.method ?? "GET" });
    if (path.startsWith("/api/product/game-review/")) {
      productRequests.push(path);
      expect(init?.method ?? "GET").toBe("GET");
      if (unavailable) return Response.json({ detail: "game_review_read_unavailable" }, { status: 503 });
      if (path === "/api/product/game-review/games") return Response.json({ games: [game] });
      if (path === detailPath) {
        expect(url.searchParams.get("repertoire_id")).toBe("rep");
        return Response.json(detail);
      }
      throw new Error(`Unexpected Games route: ${path}`);
    }
    if (path === "/api/repertoires") return Response.json({ repertoires: [{ id: "rep", name: "Opening" }] });
    return Response.json({ study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 2,
      tactics_new_per_day: 5, include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 });
  }));
  vi.mocked(fetchAndInitializeQueue).mockImplementation(async () => {
    // The synthetic owner returns the same issued entry on navigation. Reusing
    // its current attempt models the existing queue reader, not a fresh issue.
    if (useTrainingStore.getState().queueReadiness !== "ready")
      useTrainingStore.getState().hydrateLocalQueue([demoCards[0]], true);
    useTrainingStore.setState({ queueReadiness: "ready" });
    return "installed";
  });
  render(<LegacyHome />);
  await waitFor(() => expect(useTrainingStore.getState().queueReadiness).toBe("ready"));
  const attempt = useTrainingStore.getState().attempt;
  const queue = useTrainingStore.getState().practiceCards;
  expect(productRequests).toEqual([]);
  fireEvent.click((await screen.findAllByRole("button", { name: "Game Review" }))[0]);
  fireEvent.click(await screen.findByRole("button", { name: /lichess · 2026-10-20/ }));
  expect(await screen.findByText("First departure at ply 1: d2d4; repertoire: e2e4.")).toBeTruthy();
  expect(screen.queryByText(/available for Study/)).toBeNull();
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
  fireEvent.click(screen.getAllByRole("button", { name: "Train" })[0]);
  unavailable = true;
  fireEvent.click(screen.getAllByRole("button", { name: "Game Review" })[0]);
  expect(await screen.findByText(/Game Review request failed \(HTTP 503\)/)).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "Train" })[0]);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(useTrainingStore.getState().practiceCards).toEqual(queue);
  expect(requests.every(request => request.method === "GET")).toBe(true);
  expect(requests.some(request => /provider|refresh/.test(request.path))).toBe(false);
  expect(productRequests).toEqual(["/api/product/game-review/games", detailPath, "/api/product/game-review/games"]);
});
