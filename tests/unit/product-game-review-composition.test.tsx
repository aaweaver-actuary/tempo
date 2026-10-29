import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import LegacyHome from "../../app/views/legacy-home";
import { fetchAndInitializeQueue } from "../../app/views/fetchAndInitializeQueue";
import { useTrainingStore } from "../../app/state/training-store";
import { demoCards } from "../../app/samples";
import fixture from "../fixtures/game-review-current-api.json";

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
vi.mock("../../app/components/service-status-panel", () => ({ ServiceStatusPanel: () => null }));
vi.mock("../../app/components/pending-discovery-saves", () => ({ PendingDiscoverySaves: () => null }));
vi.mock("../../app/components/integrity-repair-status", () => ({ IntegrityRepairStatus: () => null }));

it("mounts current Game Review receipts and keeps the Study attempt when its optional read fails", async () => {
  let unavailable = false;
  const productRequests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://fixture").pathname;
    if (path.startsWith("/api/product/game-review/")) {
      productRequests.push(path);
      expect(init?.method).toBe("GET");
      if (unavailable) return Response.json({ detail: "game_review_read_unavailable" }, { status: 503 });
      if (path === "/api/product/game-review/games") return Response.json(fixture.recent);
      if (path === fixture.detail_path) return Response.json(fixture.detail);
      throw new Error(`Unexpected Games route: ${path}`);
    }
    if (path === "/api/repertoires") return Response.json({ repertoires: [] });
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
  expect(productRequests).toEqual([]);
  fireEvent.click((await screen.findAllByRole("button", { name: "Games" }))[0]);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(`Lichess · ${fixture.detail.game_id}`) }));
  expect(await screen.findByText(/Disposition: first introduction · Admission: pending/)).toBeTruthy();
  expect(screen.queryByText(/available for Study/)).toBeNull();
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
  fireEvent.click(screen.getAllByRole("button", { name: "Train" })[0]);
  unavailable = true;
  fireEvent.click(screen.getAllByRole("button", { name: "Games" })[0]);
  expect(await screen.findByText(/Game Review request failed \(HTTP 503\)/)).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "Train" })[0]);
  expect(useTrainingStore.getState().attempt).toEqual(attempt);
  expect(useTrainingStore.getState().queueReadiness).toBe("ready");
  expect(productRequests).toEqual(["/api/product/game-review/games", fixture.detail_path, "/api/product/game-review/games"]);
});
