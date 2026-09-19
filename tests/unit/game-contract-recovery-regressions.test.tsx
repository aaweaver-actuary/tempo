import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { Chess } from "chess.js";
import { DataDiagnosticsNotice } from "../../app/components/data-diagnostics-notice";
import { validateWorkspacePayload } from "../../app/domain/adapters/workspace-adapters";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  reportDataDiagnostic,
} from "../../app/lib/validated-data";
import {
  readWorkspaceData,
  resetWorkspaceCache,
} from "../../app/lib/workspace-data";

const summaryUrl = "http://127.0.0.1:8000/api/games/summary";
const publicGame = {
  id: "chess.com:game-1",
  provider: "chess.com" as const,
  username: "TempoPlayer",
  played_at: "2026-09-19T12:00:00+00:00",
  speed: "rapid",
  rated: true,
  color: "white" as const,
  result: "1-0",
  start_fen: new Chess().fen(),
  moves: ["e2e4", "e7e5"],
  game_url: "https://www.chess.com/game/live/1",
  opening_name: null,
  analysis_state: "pending" as const,
  analysis_version: 0,
  major_mistake_ply: null,
  missed_punishment_ply: null,
  repertoire_id: null,
  classification: null,
  divergence_ply: null,
  divergence_fen: null,
  expected: [],
  actual_uci: null,
  deviation_card_id: null,
  matched_player_decisions: null,
  repertoire_opportunities: null,
  deepest_covered_ply: null,
  first_opponent_gap_ply: null,
  out_of_book_ply: null,
  timeline: [],
  adherence: null,
};
const summaryGame = {
  id: publicGame.id,
  provider: publicGame.provider,
  played_at: publicGame.played_at,
  speed: publicGame.speed,
  color: publicGame.color,
  result: publicGame.result,
  opening_name: publicGame.opening_name,
  analysis_state: publicGame.analysis_state,
  major_mistake_ply: publicGame.major_mistake_ply,
  missed_punishment_ply: publicGame.missed_punishment_ply,
  repertoire_id: publicGame.repertoire_id,
  classification: publicGame.classification,
  divergence_ply: publicGame.divergence_ply,
  matched_player_decisions: publicGame.matched_player_decisions,
  repertoire_opportunities: publicGame.repertoire_opportunities,
  adherence: publicGame.adherence,
};
const summaryEnvelope = (games: typeof summaryGame[]) => ({
  total: games.length,
  games,
  next_cursor: null,
  aggregates: { page_count: games.length },
});

beforeEach(() => {
  localStorage.clear();
  clearDataDiagnostics();
  resetWorkspaceCache();
});

it("backend game response and strict frontend schema remain in parity", () => {
  expect(
    validateWorkspacePayload(summaryUrl, summaryEnvelope([summaryGame])),
  ).toEqual(summaryEnvelope([summaryGame]));
});

it("game contract drift fails once instead of silently emptying the library", () => {
  expect(() =>
    validateWorkspacePayload(summaryUrl, {
      ...summaryEnvelope([]),
      total: 1,
      games: [{ ...summaryGame, provider_game_id: "private" }],
    }),
  ).toThrow(/Invalid games data/);
  expect(dataDiagnostics()).toHaveLength(1);
  expect(dataDiagnostics()[0].message).toContain("provider_game_id");
});

it("invalid game responses are never cached as empty success", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...summaryEnvelope([]),
        total: 1,
        games: [{ ...summaryGame, content_hash: "private" }],
      }),
    ),
  );
  await expect(readWorkspaceData(summaryUrl)).rejects.toThrow(/Invalid games data/);
  expect(
    Array.from({ length: localStorage.length }, (_, index) =>
      localStorage.key(index),
    ).filter((key) => key?.includes("games/summary")),
  ).toEqual([]);
});

it("corrected game data replaces stale cache without navigation", async () => {
  const staleGame = { ...summaryGame, id: "chess.com:stale" };
  localStorage.setItem(
    `tempo-workspace-cache-v2:${summaryUrl}`,
    JSON.stringify({
      version: 2,
      savedAt: Date.now(),
      data: summaryEnvelope([staleGame]),
    }),
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(summaryEnvelope([summaryGame]))),
  );
  const ready = new Promise<void>((resolve) => {
    window.addEventListener(
      "tempo-workspace-data",
      (event) => {
        const detail = (event as CustomEvent<{ state: string }>).detail;
        if (detail.state === "ready") resolve();
      },
      { once: false },
    );
  });
  const immediate = (await readWorkspaceData(summaryUrl)) as {
    games: Array<{ id: string }>;
  };
  expect(immediate.games[0].id).toBe("chess.com:stale");
  await ready;
  const persisted = JSON.parse(
    localStorage.getItem(`tempo-workspace-cache-v2:${summaryUrl}`) ?? "{}",
  );
  expect(persisted.data.games[0].id).toBe(summaryGame.id);
});

it("repeated diagnostics are grouped and clear after successful validation", async () => {
  for (const recordId of ["one", "two", "three"])
    reportDataDiagnostic(
      "game",
      { id: recordId },
      'record: Unrecognized key: "content_hash"',
      recordId,
    );
  render(<DataDiagnosticsNotice />);
  expect(screen.getByText(/1 data issue type affecting 3 records/)).toBeTruthy();
  expect(screen.getByText(/examples one, two, three/)).toBeTruthy();

  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(summaryEnvelope([summaryGame]))),
  );
  await readWorkspaceData(summaryUrl);
  await waitFor(() => expect(dataDiagnostics()).toHaveLength(0));
});
