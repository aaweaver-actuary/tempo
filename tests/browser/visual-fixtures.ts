import type { Page } from "@playwright/test";
import { prepareUI } from "./ui-fixtures";
const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"];
export async function prepareVisualUI(page: Page, fixedClock = true) {
  if (fixedClock)
    await page.clock.setFixedTime(new Date("2026-09-18T16:00:00Z"));
  await page.addInitScript(() => {
    let seed = 42;
    Math.random = () => {
      seed = (seed * 16807) % 2147483647;
      return (seed - 1) / 2147483646;
    };
    localStorage.setItem("tempo-sound", "false");
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const settings = {
      initial_depth: 6,
      timezone: "America/New_York",
      new_cards_per_day: 10,
      lichess_username: "",
      chesscom_username: "",
      auto_sync_minutes: 3,
      engine_line_window_cp: 30,
      major_mistake_cp: 100,
      light_first_interval_days: 7,
      draw_hold_user_moves: 20,
    };
    const fixtures: Record<string, unknown> = {
      "/api/health": {
        status: "ok",
        storage: "local-sqlite",
        test_instance: true,
      },
      "/api/settings": settings,
      "/api/queue/today": {
        cards: [
          {
            id: "visual-card",
            queue_entry_id: 1,
            start_fen: startFen,
            moves,
            content_type: "opening",
            repertoire_name: "Spanish opening",
            repertoire_source: "PGN",
            first_correct_at: "2026-09-17T12:00:00Z",
            trained_color: "white",
          },
        ],
      },
      "/api/repertoires": {
        repertoires: [
          {
            id: "visual-repertoire",
            name: "Spanish opening — tournament preparation",
            source_name: "Spanish.pgn",
            line_count: 3,
            card_count: 8,
            due_count: 1,
            trained_color: "white",
          },
        ],
      },
      "/api/repertoire/lines": {
        lines: [
          {
            id: "visual-line",
            repertoire_id: "visual-repertoire",
            repertoire_name: "Spanish opening",
            name: "Main line",
            trained_color: "white",
            start_fen: startFen,
            moves,
          },
        ],
      },
      "/api/progress": {
        states: { new: 4, learning: 3, mature: 1, locked: 0 },
        activity: [
          { date: "2026-09-16", count: 3 },
          { date: "2026-09-17", count: 5 },
          { date: "2026-09-18", count: 2 },
        ],
        reviewedToday: 2,
        cleanCards: 1,
        dueToday: 1,
        totalCards: 8,
      },
      "/api/games/summary": {
        total: 1,
        games: [
          {
            id: "visual-game",
            provider: "lichess",
            username: "player",
            played_at: "2026-09-18T12:00:00Z",
            speed: "rapid",
            rated: true,
            color: "white",
            result: "1-0",
            start_fen: startFen,
            moves,
            opening_name: "Spanish opening",
            analysis_state: "complete",
            classification: "covered",
            repertoire_id: "visual-repertoire",
            divergence_ply: null,
          },
        ],
      },
      "/api/endgames/probe": { category: "win", moves: [] },
      "/api/endgames/templates": { templates: [] },
      "/api/tactics/catalog": {
        version: 1,
        groups: [
          { id: "basic", name: "Basic motifs" },
          { id: "advanced", name: "Advanced motifs" },
          { id: "calculation", name: "Calculation" },
          { id: "mating-depth", name: "Mating depth" },
          { id: "endgames", name: "Endgame tactics" },
          { id: "named-mates", name: "Named mates" },
        ],
        themes: [
          { id: "hangingPiece", name: "Hanging pieces", group: "basic" },
        ],
        packs: Array.from({ length: 4 }, (_, index) => ({
          id: `hangingPiece-easy-0${index + 1}`,
          theme: "hangingPiece",
          group: "basic",
          difficulty: "easy",
          ordinal: index + 1,
          count: 25,
          minRating: 700,
          maxRating: 1100,
          asset: `data/tactics-packs/hangingPiece-easy-0${index + 1}.json`,
          legacyDeckId: "hangingPiece-easy",
          active: index === 0,
          clean: index * 3,
          introduced: index * 2,
          due: index,
        })),
      },
      "/api/tactics/progress": {},
      "/api/games/sync/status": { providers: [] },
      "/api/games/analysis/claim": { job: null },
      "/api/game-findings": { findings: [] },
      "/api/game-insights/motifs": { recommendations: [] },
    };
    await route.fulfill({
      json:
        fixtures[path] ??
        (path.endsWith("/teaching")
          ? { states: [] }
          : path.includes("annotations")
            ? { annotations: [] }
            : {}),
    });
  });
  await prepareUI(page);
}
