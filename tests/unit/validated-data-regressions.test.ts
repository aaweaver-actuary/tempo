import { beforeEach, expect, it } from "vitest";
import { Chess } from "chess.js";
import { queueCardsFromPayload } from "../../app/domain/adapters/practice-card-adapters";
import {
  adaptEngineMoves,
  adaptExplorerMoves,
} from "../../app/domain/adapters/analysis-adapters";
import {
  builderSessionSchema,
  gameAnalysisClaimSchema,
  portableSnapshotSchema,
  repertoiresResponseSchema,
} from "../../app/domain/schemas";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  readStoredValue,
  validRecords,
} from "../../app/lib/validated-data";

beforeEach(clearDataDiagnostics);
const rawCard = {
  id: "saved",
  queue_entry_id: 42,
  start_fen: new Chess().fen(),
  moves: ["e2e4"],
  content_type: "opening",
  repertoire_name: "Prep",
  repertoire_source: "PGN",
};

const rawRepertoire = {
  id: "rep",
  name: "Weighted repertoire",
  source_name: "weighted.pgn",
  line_count: 12,
  card_count: 8,
  due_count: 3,
};

it("test_fractional_personal_game_evidence_is_valid_repertoire_data", () => {
  const parsed = repertoiresResponseSchema.parse({
    repertoires: [
      {
        ...rawRepertoire,
        introduction_priority: {
          state: "partial",
          personal_games: 637.74,
          explorer: "unknown",
          maia: "unknown",
          updated_at: "2026-09-21T00:56:50.367084+00:00",
          error: null,
        },
      },
    ],
  });

  expect(parsed.repertoires[0].introduction_priority?.personal_games).toBe(
    637.74,
  );
});

it("test_workspace_validation_does_not_drop_fractional_priority_records", () => {
  const records = validRecords(
    repertoiresResponseSchema.shape.repertoires.element,
    [
      {
        ...rawRepertoire,
        id: "black",
        introduction_priority: {
          state: "partial",
          personal_games: 637.74,
          explorer: "unknown",
          maia: "unknown",
          updated_at: null,
          error: null,
        },
      },
      {
        ...rawRepertoire,
        id: "white",
        introduction_priority: {
          state: "partial",
          personal_games: 299.74,
          explorer: "unknown",
          maia: "unknown",
          updated_at: null,
          error: null,
        },
      },
      {
        ...rawRepertoire,
        id: "malformed-optional-priority",
        introduction_priority: {
          state: "partial",
          personal_games: "not-a-number",
        },
      },
    ],
    "repertoire",
  );

  expect(records.map((record) => record.id)).toEqual([
    "black",
    "white",
    "malformed-optional-priority",
  ]);
  expect(records[2].introduction_priority).toBeUndefined();
  expect(dataDiagnostics()).toEqual([]);
});

it("malformed FEN, null UCI and illegal queue lines are quarantined without discarding valid study cards", () => {
  const cards = queueCardsFromPayload({
    cards: [
      rawCard,
      { ...rawCard, id: "bad-fen", start_fen: "broken" },
      { ...rawCard, id: "null", moves: ["0000"] },
      { ...rawCard, id: "illegal", moves: ["e2e5"] },
    ],
  });
  expect(cards).toHaveLength(1);
  expect(cards[0].queueEntryId).toBe(42);
  expect(dataDiagnostics()).toHaveLength(3);
  expect(dataDiagnostics().map((issue) => issue.recordId)).toEqual([
    "bad-fen",
    "null",
    "illegal",
  ]);
});

it("game analysis claims accept the evidence version returned by the backend", () => {
  const result = gameAnalysisClaimSchema.safeParse({
    job: {
      game_id: "lichess:LiV8mg66",
      analysis_version: 2,
      analysis_evidence_version: 2,
      provider: "lichess",
      username: "andy_andy_andy",
      played_at: "2026-09-19T20:08:02+00:00",
      color: "black",
      start_fen: new Chess().fen(),
      moves_json: '["d2d4"]',
      moves: ["d2d4"],
      divergence_ply: null,
      lease_id: "6228ee96-a27f-49ce-9711-c40863913011",
      lease_expires_at: "2026-09-19T23:13:14.567917+00:00",
    },
  });
  expect(result.success).toBe(true);
});

it("controlled queue payload structural drift produces a named diagnostic instead of unsafe domain values", () => {
  expect(
    queueCardsFromPayload({
      cards: [
        { ...rawCard, queue_entry_id: "42" },
        { ...rawCard, unrecognized: "value" },
      ],
    }),
  ).toEqual([]);
  expect(dataDiagnostics()).toHaveLength(2);
});

it("gameplay-prioritized queue cards remain valid typed study cards", () => {
  const cards = queueCardsFromPayload({
    cards: [
      {
        ...rawCard,
        attempt_state: "gameplay",
        introduced_at: "2026-09-19",
      },
    ],
  });

  expect(cards).toHaveLength(1);
  expect(cards[0].queueAttemptState).toBe("gameplay");
  expect(dataDiagnostics()).toEqual([]);
});

it("queue cards accept persisted pending-validation state without a diagnostic", () => {
  const cards = queueCardsFromPayload({
    cards: [{ ...rawCard, pending_validation: 0 }],
  });

  expect(cards).toHaveLength(1);
  expect(dataDiagnostics()).toEqual([]);
});

it("third-party analysis accepts new provider fields but rejects invalid counts, probabilities and illegal moves", () => {
  const fen = new Chess().fen();
  expect(
    adaptExplorerMoves(fen, [
      { uci: "e2e4", white: 4, draws: 3, black: 2, providerAddition: true },
      { uci: "e2e5", white: 1, draws: 1, black: 1 },
      { uci: "d2d4", white: -1, draws: 0, black: 0 },
    ]),
  ).toHaveLength(1);
  expect(
    adaptEngineMoves(fen, [
      { uci: "e2e4", san: "e4", probability: 2 },
      { uci: "0000", san: "" },
      { uci: "e2e4", san: "e4", probability: 0.6, pv: ["e2e4", "e7e5"] },
    ]),
  ).toHaveLength(1);
  expect(dataDiagnostics()).toHaveLength(4);
});

it("malformed stored Builder state is retained for repair while a valid default remains available", () => {
  localStorage.setItem(
    "tempo-builder-session",
    JSON.stringify({ version: 1, history: [{ uci: "0000" }] }),
  );
  expect(
    readStoredValue(
      localStorage,
      "tempo-builder-session",
      builderSessionSchema,
    ),
  ).toBeUndefined();
  expect(localStorage.getItem("tempo-builder-session")).toContain("0000");
  expect(dataDiagnostics()[0].source).toBe("storage:tempo-builder-session");
});

it("backup snapshots require all version, timestamp, checksum, table, and count fields", () => {
  expect(
    portableSnapshotSchema.safeParse({
      schemaVersion: 1,
      tables: {},
      counts: {},
    }).success,
  ).toBe(false);
});
