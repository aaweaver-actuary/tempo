import type { Page } from "@playwright/test";
import type { ActivityResponse } from "../../app/lib/service-status";
import { prepareUI } from "./ui-fixtures";
const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"];
type VisualQueueCard = {
  id: string; queue_entry_id: number; start_fen: string; moves: string[];
  content_type: "opening"; repertoire_name: string; repertoire_source: string;
  first_correct_at: string; trained_color: "white";
};
export type VisualRepertoireLine = {
  id: string; repertoire_id: string; repertoire_name: string; name: string;
  trained_color: "white"; start_fen: string; moves: string[];
};
export async function prepareVisualUI(page: Page, fixedClock = true, trainingCards?: VisualQueueCard[], repertoireLines?: VisualRepertoireLine[], activityResponse?: ActivityResponse) {
  let activeTrainingCards = trainingCards ?? [{
    id: "visual-card",
    queue_entry_id: 1,
    start_fen: startFen,
    moves,
    content_type: "opening" as const,
    repertoire_name: "Spanish opening",
    repertoire_source: "PGN",
    first_correct_at: "2026-09-17T12:00:00Z",
    trained_color: "white" as const,
  }];
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
    // Mock review POSTs have no server receipt unless the test installs one.
    if (/^\/api\/operations\/review-(?:attempt|reconcile):/.test(decodeURIComponent(path))) {
      await route.fulfill({ status: 404, json: {} });
      return;
    }
    if (trainingCards && route.request().method() === "POST" && /^\/api\/cards\/[^/]+\/review$/.test(path)) {
      const reviewedCardId = path.split("/")[3];
      activeTrainingCards = activeTrainingCards.filter((card) => card.id !== reviewedCardId);
      await route.fulfill({ json: { persisted: true } });
      return;
    }
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
    };
    const fixtures: Record<string, unknown> = {
      "/api/health": {
        status: "ok",
        storage: "local-sqlite",
        test_instance: true,
      },
      "/api/settings": settings,
      "/api/queue/window": {
        cards: activeTrainingCards,
        count: activeTrainingCards.length,
      },
      "/api/queue/prepared": {
        prepared_at: "2026-09-18T16:00:00Z",
        local_date: "2026-09-18",
        count: activeTrainingCards.length,
        projection: { state: "ready", generation: 1, updated_at: "2026-09-18T16:00:00Z",
          refresh_pending: 0, last_error: null, blocked_count: 0 },
        cards: activeTrainingCards.map((card) => ({ ...card, latest_review_id: 0 })),
      },
      "/api/repertoires": {
        repertoires: [
          {
            id: "visual-repertoire",
            name: "Spanish opening — tournament preparation",
            new_cards_per_day: null,
            effective_new_cards_per_day: 10,
            source_name: "Spanish.pgn",
            line_count: repertoireLines?.length ?? 3,
            card_count: 8,
            active_prefix_count: 2,
            graph_updated_at: "2026-09-18T12:00:00Z",
            due_count: 1,
            trained_color: "white",
          },
        ],
      },
      "/api/repertoires/visual-repertoire/statistics": {
        window: "90d", graph_updated_at: "2026-09-18T12:00:00Z", graph_state: "ready", game_state: "ready",
        prefix: { total: 3, active: 2, studied: 1, unseen: 2, locked: 1, paused: 0 },
        cards: { total: 8, new: 2, learning: 3, mature: 2, locked: 1, difficult: 1, due_today: 1, due_next_seven_days: 2 },
        study: { correct: 4, attempts: 5, accuracy: 0.8 },
        games: { matched: 3, correct: 2, decisions: 3, adherence: 2 / 3, wins: 2, draws: 0, losses: 1, positions_seen: 1, positions_total: 4 },
        unlocks: [{ card_id: "child", parent_card_id: "parent", line_name: "Spanish continuation", parent_due_date: "2026-09-19", earliest_unlock_date: "2026-09-22", status: "ready" }],
      },
      "/api/repertoires/visual-repertoire/statistics/positions": {
        positions: [{ fen_key: startFen.split(" ").slice(0, 4).join(" "), fen: startFen,
          games: 3, encounters: 3, correct: 2, missed: 1, last_seen_at: "2026-09-18T12:00:00Z",
          expected_moves: ["e2e4"], played_moves: [{ move_uci: "e2e4", count: 2 }, { move_uci: "d2d4", count: 1 }],
          card_id: "visual-card", sample_game_id: "visual-game", sample_ply: 0 }],
        total: 1, next_cursor: null,
      },
      "/api/repertoire/lines": {
        lines: repertoireLines ?? [
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
      "/api/games/position-summary": { encounters: 0, analyzed_encounters: 0, moves: [] },
      "/api/system/activity": activityResponse ?? { items: [], counts: { running: 0, queued: 0, paused: 0, failed: 0 }, total: 0, next_offset: null, writer: { healthy: true, foreground: 0, background: 0 } },
      "/api/games/analysis/claim": { job: null },
      "/api/game-findings": { findings: [] },
      "/api/discoveries": { discoveries: [], total: 0, next_offset: null, unread_count: 0 },
      "/api/game-findings/tactical-queue": { item: null, remaining: 0 },
      "/api/defensive-threats/candidates": { candidates: [] },
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
