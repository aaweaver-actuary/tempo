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
      "/api/statistics/overview": {
        window_days: 30,
        games: 24,
        analyzed_games: 21,
        analysis_coverage: 0.875,
        score: { value: 0.604, wins: 12, draws: 5, losses: 7, numerator: 14.5, denominator: 24, confidence_interval: [0.408, 0.8] },
        decision_quality: { mean_loss_cp: 43.2, major_mistakes_per_game: 0.71, denominator: 21 },
        tactical_performance: { value: 0.68, found: 17, opportunities: 25, conceded_per_100_decisions: 2.4, conceded: 11, player_decisions: 458, confidence_interval: [0.497, 0.863] },
      },
      "/api/statistics/breakdown": {
        dimension: "color",
        window_days: 30,
        segments: [
          { segment: "white", games: 13, score: 0.654, mean_loss_cp: 39.8, tactical_found: 10, tactical_opportunities: 14 },
          { segment: "black", games: 11, score: 0.545, mean_loss_cp: 47.1, tactical_found: 7, tactical_opportunities: 11 },
        ],
      },
      "/api/games/summary": {
        total: 1,
        next_cursor: null,
        aggregates: { page_count: 1 },
        games: [
          {
            id: "visual-game",
            provider: "lichess",
            played_at: "2026-09-18T12:00:00Z",
            speed: "rapid",
            color: "white",
            result: "1-0",
            opening_name: "Spanish opening",
            analysis_state: "complete",
            major_mistake_ply: null,
            missed_punishment_ply: null,
            classification: "covered",
            repertoire_id: "visual-repertoire",
            divergence_ply: null,
            matched_player_decisions: 3,
            repertoire_opportunities: 3,
            adherence: 1,
          },
        ],
      },
      "/api/games/visual-game": {
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
        game_url: null,
        opening_name: "Spanish opening",
        analysis_state: "complete",
        analysis_version: 1,
        major_mistake_ply: null,
        missed_punishment_ply: null,
        repertoire_id: "visual-repertoire",
        classification: "covered",
        divergence_ply: null,
        divergence_fen: null,
        expected: [],
        actual_uci: null,
        deviation_card_id: null,
        matched_player_decisions: 3,
        repertoire_opportunities: 3,
        deepest_covered_ply: 6,
        first_opponent_gap_ply: null,
        out_of_book_ply: null,
        timeline: [],
        adherence: 1,
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
      "/api/system/activity": { items: [], counts: { running: 0, queued: 0, paused: 0, failed: 0 }, total: 0, next_offset: null, writer: { healthy: true, foreground: 0, background: 0 } },
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
