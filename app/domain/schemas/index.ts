import * as z from "zod";
import { QueueAttemptState } from "../cards";
import {
  cardIdSchema,
  colorSchema,
  deckIdSchema,
  fenKeySchema,
  fenStringSchema,
  gameIdSchema,
  identifierSchema,
  isoDateSchema,
  lineIdSchema,
  queueEntryIdSchema,
  repertoireIdSchema,
  sanMoveSchema,
  squareSchema,
  sqliteBooleanSchema,
  uciMoveSchema,
} from "./primitives";
export * from "./primitives";

const integer = z.number().int().nonnegative();
const nullableDate = isoDateSchema.nullable().optional();
export const attemptStateSchema = z.enum(QueueAttemptState);
export const queueCardSchema = z.strictObject({
  id: identifierSchema,
  queue_entry_id: queueEntryIdSchema,
  start_fen: fenStringSchema,
  moves: z.array(z.string()),
  content_type: z.enum(["opening", "middlegame", "endgame", "tactic"]),
  repertoire_name: z.string(),
  repertoire_source: z.string(),
  cycle: integer.optional(),
  position: integer.optional(),
  attempt_state: attemptStateSchema.optional(),
  attempt_failed: sqliteBooleanSchema.optional(),
  first_correct_at: nullableDate,
  recent_attempts_json: z.string().optional(),
  source_ref: z.string().nullable().optional(),
  is_main: sqliteBooleanSchema.optional(),
  trained_color: colorSchema.nullable().optional(),
  revision: z.number().int().positive().optional(),
  repertoire_id: identifierSchema.optional(),
  kind: z.enum(["prefix", "response", "checkpoint"]).optional(),
  state: z.enum(["locked", "new", "learning", "mature"]).optional(),
  due_date: z.iso.date().optional(),
  interval_days: integer.optional(),
  ease: z.number().finite().positive().optional(),
  repetitions: integer.optional(),
  lapses: integer.optional(),
  unlock_after_card_id: z.string().nullable().optional(),
  fsrs_card_json: z.string().nullable().optional(),
  reinforcement_pending: sqliteBooleanSchema.optional(),
  stability: z.number().finite().nonnegative().optional(),
  guided_review: sqliteBooleanSchema.optional(),
  maximum_interval: z.number().int().positive().optional(),
  scheduling_mode: z.enum(["normal", "light", "hard"]).optional(),
  hard_correct_streak: integer.optional(),
  archived: sqliteBooleanSchema.optional(),
  superseded_by: z.string().nullable().optional(),
  source_fen: z.string().nullable().optional(),
  introduced_at: z.iso.date().nullable().optional(),
  pending_validation: sqliteBooleanSchema.optional(),
});
export const queueEnvelopeSchema = z.strictObject({
  cards: z.array(z.unknown()),
  local_date: z.iso.date().optional(),
  count: integer.optional(),
  diagnostics: z
    .array(
      z.strictObject({
        card_id: identifierSchema,
        message: z.string().min(1),
      }),
    )
    .optional(),
  projection: z
    .strictObject({
      queue_date: z.iso.date().optional(),
      state: z.enum(["ready", "refreshing", "failed"]),
      generation: integer,
      updated_at: z.iso.datetime({ offset: true }).nullable(),
      refresh_pending: sqliteBooleanSchema,
      last_error: z.string().nullable(),
      blocked_count: integer.optional(),
    })
    .optional(),
});
export const packagedPuzzleSchema = z.strictObject({
  DeckId: deckIdSchema,
  DeckPosition: z.number().int().positive(),
  LegacyDeckId: z.string().optional(),
  LegacyDeckPosition: z.number().int().positive().optional(),
  PuzzleId: identifierSchema,
  FEN: fenStringSchema,
  Moves: z.string().trim().min(1),
  Rating: z.number().int().nonnegative(),
  Motif: identifierSchema.optional(),
  Difficulty: z
    .enum(["easy", "medium", "hard", "focused", "advanced"])
    .optional(),
  RatingDeviation: integer.optional(),
  Popularity: z.number().int().min(-100).max(100).optional(),
  NbPlays: integer.optional(),
  Themes: z.array(z.string()).optional(),
  GameUrl: z.url().optional(),
});
export const explorerMoveSchema = z.looseObject({
  uci: uciMoveSchema,
  san: z.string().optional(),
  white: integer,
  draws: integer,
  black: integer,
});
export const explorerResponseSchema = z.looseObject({
  moves: z.array(z.unknown()),
  white: integer.optional(),
  draws: integer.optional(),
  black: integer.optional(),
});
export const engineMoveSchema = z.looseObject({
  uci: uciMoveSchema,
  san: z.string().optional(),
  probability: z.number().finite().min(0).max(1).optional(),
  score: z.string().optional(),
  cp: z.number().finite().optional(),
  mate: z.number().int().optional(),
  // PVs are canonicalized move-by-move so one trailing provider token does not
  // discard an otherwise useful engine line.
  pv: z.array(z.string()).optional(),
});
export const stockfishMessageSchema = z.strictObject({
  type: z.enum(["line", "ready", "error", "cancelled"]),
  id: integer.optional(),
  line: z.string().optional(),
  message: z.string().optional(),
});
const arrowColorSchema = z.enum(["green", "red", "blue", "yellow"]);
export const annotationSchema = z.strictObject({
  repertoireId: repertoireIdSchema,
  fenKey: fenKeySchema,
  comment: z.string().max(4000),
  arrows: z.array(
    z.strictObject({
      from: squareSchema,
      to: squareSchema,
      color: arrowColorSchema,
    }),
  ),
  squares: z.array(
    z.strictObject({ square: squareSchema, color: arrowColorSchema }),
  ),
  updatedAt: isoDateSchema,
});
export const annotationsResponseSchema = z.strictObject({
  annotations: z.array(z.unknown()),
});
export const practiceCardSchema = z.strictObject({
  id: cardIdSchema,
  kind: z.enum(["opening", "middlegame", "endgame", "puzzle"]),
  title: z.string(),
  subtitle: z.string(),
  startingFen: fenStringSchema,
  moves: z.array(sanMoveSchema),
  userMoveTarget: integer,
  nextMove: sanMoveSchema.optional(),
  sourceUrl: z.string().optional(),
  backendId: cardIdSchema.optional(),
  queueEntryId: queueEntryIdSchema.optional(),
  queueCycle: integer.optional(),
  queueAttemptState: attemptStateSchema.optional(),
  attemptFailed: z.boolean().optional(),
  firstCleanPassAt: isoDateSchema.optional(),
  suggestShorterPrefix: z.boolean().optional(),
  orientation: colorSchema.optional(),
  revision: z.number().int().positive().optional(),
  repertoireId: repertoireIdSchema.optional(),
  editingIntent: z.enum(["standard", "shorten-prefix"]).optional(),
});
export const localRepertoireSchema = z.strictObject({
  id: repertoireIdSchema,
  title: z.string(),
  sourceName: z.string(),
  side: colorSchema,
  pgn: z.string(),
  cards: z.array(practiceCardSchema),
});
export const builderSessionSchema = z
  .strictObject({
    version: z.literal(1),
    activeRepertoireByColor: z.strictObject({
      white: repertoireIdSchema.optional(),
      black: repertoireIdSchema.optional(),
    }),
    activeRepertoireId: repertoireIdSchema.optional(),
    orientation: colorSchema,
    startingFen: fenStringSchema,
    history: z.array(
      z.strictObject({
        san: sanMoveSchema,
        uci: uciMoveSchema,
        fen: fenStringSchema,
      }),
    ),
    cursor: integer,
    branchStart: integer.nullable(),
    dismissedTranspositions: z.array(z.string()).optional(),
    sourceGapId: z.string().optional(),
  })
  .refine(
    (value) =>
      value.cursor <= value.history.length &&
      (value.branchStart === null || value.branchStart <= value.history.length),
    "Builder cursor exceeds history",
  );
export const repertoireLineSchema = z.strictObject({
  id: lineIdSchema,
  repertoire_id: repertoireIdSchema,
  repertoire_name: z.string(),
  name: z.string(),
  trained_color: colorSchema,
  start_fen: fenStringSchema,
  moves: z.array(z.string()),
  moves_json: z.string().optional(),
  is_main: sqliteBooleanSchema.optional(),
});
export const repertoireLinesResponseSchema = z.strictObject({
  lines: z.array(z.unknown()),
});
export const repertoiresResponseSchema = z.strictObject({
  repertoires: z.array(
    z.strictObject({
      id: repertoireIdSchema,
      name: z.string(),
      source_name: z.string(),
      created_at: isoDateSchema.optional(),
      is_main: sqliteBooleanSchema.optional(),
      line_count: integer,
      card_count: integer,
      due_count: integer,
      blocked_due_count: integer.optional(),
      blocked_card_count: integer.optional(),
      conflict_count: integer.optional(),
      integrity_status: z.enum(["unchecked", "clean", "needs_repair"]).optional(),
      integrity_issue_count: integer.optional(),
      integrity_first_issue_id: z.string().nullable().optional(),
      trained_color: colorSchema.nullable().optional(),
      introduction_priority: z.strictObject({
        state: z.enum(["fallback", "partial", "ready"]),
        // Recency decay makes this an effective (weighted) game count.
        personal_games: z.number().finite().nonnegative(),
        explorer: z.string(),
        maia: z.string(),
        updated_at: z.string().nullable(),
        error: z.string().nullable(),
      }).optional().catch(undefined),
      graph_generation: integer.optional(),
      graph_updated_at: isoDateSchema.nullable().optional(),
      graph_state: z.enum(["refreshing", "ready", "failed"]).optional(),
      graph_error: z.string().nullable().optional(),
    }),
  ),
});
export const settingsResponseSchema = z.strictObject({
  initial_depth: z.number().int().min(2).max(20),
  timezone: z.string(),
  new_cards_per_day: z.number().int().min(0).max(100),
  tactics_new_per_day: z.number().int().min(0).max(100).default(5),
  lichess_username: z.string(),
  chesscom_username: z.string(),
  auto_sync_minutes: z.number().int().min(2).max(60),
  engine_line_window_cp: z.number().int().min(0).max(300),
  major_mistake_cp: z.number().int().min(25).max(1000),
  light_first_interval_days: z.number().int().min(1).max(90),
  draw_hold_user_moves: z.number().int().min(5).max(100),
  coverage_reply_denominator: z.number().int().min(2).max(10000).default(100),
  coverage_cumulative_target: z.number().int().min(50).max(100).default(95),
  coverage_horizon_fullmoves: z.number().int().min(4).max(40).default(15),
  coverage_path_floor: z.number().min(0).max(0.1).default(0.0005),
  coverage_maia_elo: z.number().int().min(1100).max(1900).default(1500),
});
export const importResultSchema = z.strictObject({
  repertoire_id: repertoireIdSchema,
  source_name: z.string(),
  games_found: integer,
  unique_lines: integer,
  cards_created: integer,
  duplicates_merged: integer,
  cards_admitted_today: integer,
  decision_cards_created: integer.optional(),
  shared_decisions_reused: integer.optional(),
  prefix_cards_created: integer.optional(),
  shared_prefixes_reused: integer.optional(),
  descendant_decision_cards_created: integer.optional(),
  graph_state: z.enum(["refreshing", "ready", "failed"]).optional(),
  integrity: z.object({
    status: z.enum(["unchecked", "clean", "needs_repair"]),
    issue_count: integer,
    first_issue_id: z.string().nullable(),
    checked_at: z.string().nullable().optional(),
    scan_status: z.enum(["idle", "queued", "running", "retrying", "failed"]).optional(),
    scan_generation: z.string().nullable().optional(),
    scan_progress: z.object({ completed: integer, total: integer }).optional(),
    last_scan_error: z.string().nullable().optional(),
  }).optional(),
});
export const integrityMoveSchema = z.strictObject({
  uci: uciMoveSchema,
  line_count: integer,
  card_count: integer,
  review_count: integer,
});
export const integrityIssueSchema = z.strictObject({
  id: z.string(),
  kind: z.enum(["missing_response", "multiple_responses", "invalid_source"]),
  fen_key: z.string().nullable(),
  fen: fenStringSchema.nullable(),
  trained_color: colorSchema.nullable(),
  signature: z.string(),
  moves: z.array(integrityMoveSchema),
  sources: z.array(z.record(z.string(), z.unknown())),
});
export const repertoireIntegritySchema = z.strictObject({
  repertoire_id: repertoireIdSchema,
  status: z.enum(["unchecked", "clean", "needs_repair"]),
  issue_count: integer,
  first_issue_id: z.string().nullable(),
  checked_at: z.string().nullable().optional(),
  scan_status: z.enum(["idle", "queued", "running", "retrying", "failed"]),
  scan_generation: z.string().nullable(),
  scan_progress: z.object({ completed: integer, total: integer }),
  last_scan_error: z.string().nullable(),
  issues: z.array(integrityIssueSchema),
});
export const integrityResolutionSchema = z.strictObject({
  summary: z.object({
    status: z.enum(["unchecked", "clean", "needs_repair"]),
    issue_count: integer,
    first_issue_id: z.string().nullable(),
    checked_at: z.string().nullable().optional(),
    scan_status: z.enum(["idle", "queued", "running", "retrying", "failed"]).optional(),
    scan_generation: z.string().nullable().optional(),
    scan_progress: z.object({ completed: integer, total: integer }).optional(),
    last_scan_error: z.string().nullable().optional(),
  }),
  changed_line_count: integer,
  changed_card_count: integer,
  next_issue: integrityIssueSchema.nullable(),
  issues_remaining: integer,
});
export const integrityRepairSubmissionSchema = z.strictObject({
  task_id: z.string(),
  repertoire_id: repertoireIdSchema,
  issue_id: z.string(),
  state: z.enum(["queued", "leased", "retrying", "complete", "failed"]),
});
export const branchResultSchema = z.strictObject({
  id: lineIdSchema,
  duplicate: z.boolean(),
  moves: z.array(uciMoveSchema),
  integrity: z.object({
    status: z.enum(["unchecked", "clean", "needs_repair"]),
    issue_count: integer,
    first_issue_id: z.string().nullable(),
    checked_at: z.string().nullable().optional(),
  }).optional(),
});
export const removeBranchResultSchema = z.strictObject({
  deleted_line_count: integer,
  deleted_card_count: integer,
  retained_line_count: integer,
  integrity: z.object({
    status: z.enum(["unchecked", "clean", "needs_repair"]),
    issue_count: integer,
    first_issue_id: z.string().nullable(),
    checked_at: z.string().nullable().optional(),
  }).optional(),
});
export const cardRevisionResultSchema = z.strictObject({
  card_id: cardIdSchema,
  replaced: z.boolean(),
  history_mode: z.enum(["preserve", "reset"]),
});
const prefixSplitCardSchema = z.strictObject({
  card_id: cardIdSchema,
  starting_fen: fenStringSchema,
  moves: z.array(uciMoveSchema),
  tested_player_moves: integer,
});
export const prefixSplitResponseSchema = z.strictObject({
  source_card_id: cardIdSchema,
  source_revision: integer,
  parent: prefixSplitCardSchema,
  continuation: prefixSplitCardSchema,
  applied: z.boolean(),
  idempotent: z.boolean(),
  shared_line_count: integer.optional(),
  shared_repertoire_count: integer.optional(),
});
export const tacticProgressSchema = z.record(
  z.string(),
  z.strictObject({
    clean: integer,
    index: integer,
    cleanIds: z.array(z.string()).optional(),
    discoveredIds: z.array(z.string()).optional(),
  }),
);
export const motifRecommendationSchema = z.strictObject({
  motif: identifierSchema,
  miss_count: integer,
  exploited_count: integer.optional(),
  opportunity_count: integer,
  miss_rate: z.number().min(0).max(1),
  conversion_rate: z.number().min(0).max(1).optional(),
  total_loss_cp: integer,
  supporting_games: z.array(gameIdSchema),
  window_days: z.number().int().positive(),
  recommended_pack_id: identifierSchema.nullable(),
  recommended_pack_active: z.boolean(),
});
export const motifRecommendationsSchema = z.strictObject({
  recommendations: z.array(motifRecommendationSchema),
});
const optionalMetricSchema = z.number().finite().nullable();
const confidenceIntervalSchema = z.tuple([z.number(), z.number()]).nullable();
export const chessStatisticsOverviewSchema = z.strictObject({
  window_days: z.number().int().positive(),
  games: integer,
  analyzed_games: integer,
  analysis_coverage: z.number().min(0).max(1),
  score: z.strictObject({
    value: optionalMetricSchema,
    wins: integer,
    draws: integer,
    losses: integer,
    numerator: z.number().nonnegative(),
    denominator: integer,
    confidence_interval: confidenceIntervalSchema,
  }),
  decision_quality: z.strictObject({
    mean_loss_cp: optionalMetricSchema,
    major_mistakes_per_game: optionalMetricSchema,
    denominator: integer,
  }),
  tactical_performance: z.strictObject({
    value: optionalMetricSchema,
    found: integer,
    opportunities: integer,
    conceded_per_100_decisions: optionalMetricSchema,
    conceded: integer,
    player_decisions: integer,
    confidence_interval: confidenceIntervalSchema,
  }),
});
export const chessStatisticsBreakdownSchema = z.strictObject({
  dimension: z.enum([
    "color", "speed", "provider", "weekday", "hour", "opening",
    "repertoire", "opponent_rating", "relative_rating",
  ]),
  window_days: z.number().int().positive(),
  segments: z.array(z.strictObject({
    segment: z.string(),
    games: integer,
    score: optionalMetricSchema,
    mean_loss_cp: optionalMetricSchema,
    tactical_found: integer,
    tactical_opportunities: integer,
  })),
});
export const teachingResponseSchema = z.strictObject({
  states: z.array(
    z.strictObject({
      cardId: identifierSchema.optional(),
      revision: z.number().int().positive(),
      ply: integer,
      taughtAt: isoDateSchema.optional(),
    }),
  ),
});

export const dataDiagnosticSchema = z.strictObject({
  source: z.string(),
  recordId: z.string().optional(),
  message: z.string(),
  raw: z.unknown(),
  recordedAt: isoDateSchema,
});
export const studyReplySchema = z.strictObject({
  id: z.number().int().positive(),
  result: z.unknown().optional(),
  error: z.string().optional(),
  diagnostics: z.array(dataDiagnosticSchema).optional(),
});
const lineValidationSchema = z.strictObject({
  isValid: z.boolean(),
  isTruncated: z.boolean(),
  diagnostics: z.array(
    z.strictObject({
      ply: integer,
      move: z.string(),
      kind: z.enum(["null", "invalid", "illegal"]),
      message: z.string(),
    }),
  ),
});
export const analysisLineSchema = z.strictObject({
  id: lineIdSchema,
  repertoireId: repertoireIdSchema,
  repertoireName: z.string(),
  title: z.string(),
  side: colorSchema,
  startingFen: fenStringSchema,
  moves: z.array(z.union([uciMoveSchema, sanMoveSchema])),
  validation: lineValidationSchema.optional(),
});
export const indexedPositionSchema = z.strictObject({
  lineId: identifierSchema,
  repertoireId: identifierSchema,
  repertoireName: z.string(),
  fen: fenStringSchema,
  ply: integer,
  nextUci: uciMoveSchema.optional(),
  distance: integer.optional(),
});
export const studyTaskSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("workspace"),
    url: z.string(),
    payload: z.unknown(),
  }),
  z.strictObject({ kind: z.literal("transportLines"), payload: z.unknown() }),
  z.strictObject({ kind: z.literal("queue"), payload: z.unknown() }),
  z.strictObject({
    kind: z.literal("deck"),
    records: z.array(z.unknown()),
    deckId: identifierSchema,
  }),
  z.strictObject({
    kind: z.literal("lines"),
    lines: z.array(analysisLineSchema),
  }),
  z.strictObject({
    kind: z.literal("index"),
    lines: z.array(analysisLineSchema),
  }),
  z.strictObject({
    kind: z.enum(["similarity", "matches"]),
    fen: fenStringSchema,
    positions: z.array(indexedPositionSchema),
  }),
]);
export const studyRequestSchema = z.strictObject({
  id: z.number().int().positive(),
  task: studyTaskSchema,
});
export const maiaRequestSchema = z.strictObject({
  id: z.number().int().positive(),
  fen: fenStringSchema,
  elo: z.number().int().min(400).max(3000),
  assetRoot: z.url(),
});
export const maiaReplySchema = z.discriminatedUnion("type", [
  z.strictObject({
    type: z.literal("progress"),
    id: z.number().int().positive(),
    progress: z.number().min(0).max(100),
  }),
  z.strictObject({
    type: z.literal("result"),
    id: z.number().int().positive(),
    moves: z.array(z.unknown()),
  }),
  z.strictObject({
    type: z.literal("error"),
    id: z.number().int().positive(),
    message: z.string(),
  }),
]);
const providerSyncResultSchema = z.strictObject({
  provider: z.enum(["lichess", "chess.com"]),
  username: z.string(),
  status: z.enum(["idle", "error"]),
  fetched: integer,
  inserted: integer,
  updated: integer,
  duplicates: integer,
  filtered: integer,
  rejected: integer,
  failed: integer,
  error: z.string().nullable(),
  retry_after: nullableDate,
});
const gameSyncJobStatusSchema = z.enum([
  "queued",
  "running",
  "paused",
  "retrying",
  "complete",
  "failed",
]);
const completedSyncResultSchema = z.strictObject({
  imported: integer,
  synced_at: isoDateSchema,
  cached: z.boolean().optional(),
  incremental: z.boolean().optional(),
  providers: z.partialRecord(
    z.enum(["lichess", "chess.com"]),
    providerSyncResultSchema,
  ),
});
export const syncResultSchema = z.strictObject({
  imported: integer,
  job_id: z.string().uuid(),
  status: gameSyncJobStatusSchema,
  providers: z.partialRecord(
    z.enum(["lichess", "chess.com"]),
    providerSyncResultSchema,
  ),
});
export const syncStatusSchema = z.strictObject({
  providers: z.array(
    z.strictObject({
      provider: z.enum(["lichess", "chess.com"]),
      username: z.string(),
      status: z.enum(["idle", "syncing", "success", "error", "synced"]),
      cursor: z.string().nullable(),
      last_started_at: nullableDate,
      last_success_at: nullableDate,
      last_error: z.string().nullable(),
      retry_after: nullableDate,
      last_result: providerSyncResultSchema.nullable().optional(),
    }),
  ),
  active_filters: z.strictObject({
    days: integer,
    speeds: z.array(z.string()),
    rated_only: z.boolean(),
  }).optional(),
  active_job: z.strictObject({
    id: z.string().uuid(),
    status: gameSyncJobStatusSchema,
    created_at: isoDateSchema,
    started_at: nullableDate,
    completed_at: nullableDate,
    updated_at: isoDateSchema,
    error: z.string().nullable(),
    result: completedSyncResultSchema.nullable(),
  }).nullable().optional(),
});
export const gameRecordSchema = z.strictObject({
  id: gameIdSchema,
  provider: z.enum(["lichess", "chess.com"]),
  username: z.string(),
  played_at: z.string(),
  speed: z.string(),
  rated: sqliteBooleanSchema,
  color: colorSchema,
  result: z.string(),
  start_fen: fenStringSchema,
  moves: z.array(uciMoveSchema),
  game_url: z.string().nullable().optional(),
  opening_name: z.string().nullable().optional(),
  analysis_state: z
    .enum(["pending", "analyzing", "ready", "complete", "failed"])
    .optional(),
  analysis_version: integer.optional(),
  major_mistake_ply: integer.nullable().optional(),
  missed_punishment_ply: integer.nullable().optional(),
  repertoire_id: repertoireIdSchema.nullable().optional(),
  classification: z.string().nullable().optional(),
  divergence_ply: integer.nullable().optional(),
  divergence_fen: z.string().nullable().optional(),
  expected: z.array(uciMoveSchema).optional(),
  actual_uci: z.string().nullable().optional(),
  deviation_card_id: z.string().nullable().optional(),
  matched_player_decisions: integer.nullable().optional(),
  repertoire_opportunities: integer.nullable().optional(),
  deepest_covered_ply: integer.nullable().optional(),
  first_opponent_gap_ply: integer.nullable().optional(),
  out_of_book_ply: integer.nullable().optional(),
  timeline: z.array(z.looseObject({ ply: integer, kind: z.string() })).optional(),
  adherence: z.number().nullable().optional(),
});
export const gamesSummarySchema = z.strictObject({
  total: integer,
  games: z.array(z.strictObject({
    id: gameIdSchema,
    provider: z.enum(["lichess", "chess.com"]),
    played_at: z.string(),
    speed: z.string(),
    color: colorSchema,
    result: z.string(),
    opening_name: z.string().nullable().optional(),
    analysis_state: z.enum(["pending", "analyzing", "ready", "complete", "failed"]),
    major_mistake_ply: integer.nullable(),
    missed_punishment_ply: integer.nullable(),
    repertoire_id: repertoireIdSchema.nullable(),
    classification: z.string().nullable(),
    divergence_ply: integer.nullable(),
    matched_player_decisions: integer.nullable(),
    repertoire_opportunities: integer.nullable(),
    adherence: z.number().nullable(),
  })),
  next_cursor: z.string().nullable(),
  aggregates: z.record(z.string(), integer),
});
export const gameAnalysisClaimSchema = z.strictObject({
  job: z.strictObject({
    game_id: gameIdSchema,
    analysis_version: integer,
    analysis_evidence_version: integer,
    provider: z.enum(["lichess", "chess.com"]),
    username: z.string(),
    played_at: z.string(),
    color: colorSchema,
    start_fen: fenStringSchema,
    moves_json: z.string(),
    moves: z.array(uciMoveSchema),
    divergence_ply: integer.nullable(),
    lease_id: z.string(),
    lease_expires_at: isoDateSchema,
  }).nullable(),
});
export const coverageMaiaClaimSchema = z.strictObject({
  job: z
    .strictObject({
      node_id: z.string(),
      lease_id: z.string(),
      fen: fenStringSchema,
      elo: integer,
    })
    .nullable(),
});
export const repertoireCoverageSummarySchema = z.strictObject({
  run_id: z.string().uuid().nullable(),
  status: z.enum(["not-started", "queued", "running", "complete", "failed"]),
  required_branches: integer,
  covered_branches: integer,
  probability_coverage: z.number().min(0).max(1).nullable(),
  is_complete: z.boolean(),
  unknown_nodes: integer,
  last_error: z.string().nullable().optional(),
  settings: z
    .strictObject({
      reply_denominator: integer,
      cumulative_target: z.number(),
      horizon_fullmoves: integer,
      path_floor: z.number(),
      maia_elo: integer,
    })
    .optional(),
});
export const repertoireCoverageGapsSchema = z.strictObject({
  gaps: z.array(
    z.strictObject({
      gap_id: z.string(),
      node_id: z.string(),
      fen: fenStringSchema,
      fen_key: fenKeySchema,
      move_uci: uciMoveSchema,
      probability: z.number().nullable(),
      source_state: z.enum(["blended", "explorer-only", "maia-only", "unknown"]),
      explorer_games: integer,
      trained_color: colorSchema,
    }),
  ),
});
export const progressResponseSchema = z.strictObject({
  states: z.record(z.string(), integer),
  activity: z.array(z.strictObject({ date: z.iso.date(), count: integer })),
  reviewedToday: integer,
  cleanCards: integer,
  dueToday: integer,
  blockedDue: integer.optional(),
  totalCards: integer,
});
const tablebaseCategorySchema = z.enum([
  "win",
  "loss",
  "draw",
  "blessed-loss",
  "cursed-win",
  "unknown",
]);
export const tablebaseResponseSchema = z.looseObject({
  category: tablebaseCategorySchema,
  moves: z
    .array(
      z.looseObject({
        uci: uciMoveSchema,
        san: sanMoveSchema.optional(),
        category: tablebaseCategorySchema.optional(),
        dtz: z.number().int().nullable().optional(),
      }),
    )
    .optional(),
});
export const endgameTemplatesSchema = z.strictObject({
  templates: z.array(
    z.strictObject({
      id: identifierSchema,
      card_id: cardIdSchema,
      name: z.string(),
      white_material: z.string(),
      black_material: z.string(),
      trained_color: colorSchema,
      goal_mix: z.enum(["win", "draw", "both"]),
      enabled: sqliteBooleanSchema,
      created_at: isoDateSchema,
    }),
  ),
});
export const endgameCreatedSchema = z.strictObject({
  id: identifierSchema,
  card_id: cardIdSchema,
  sample_fen: fenStringSchema,
  already_exists: z.boolean(),
});
export const endgameAttemptSchema = z.strictObject({
  id: identifierSchema,
  fen: fenStringSchema,
  target: z.enum(["win", "draw"]),
  moves: z.array(z.unknown()),
});
export const oauthTokenSchema = z.looseObject({
  access_token: z.string().min(1),
});
export const storedRecordSchema = z.strictObject({
  key: identifierSchema,
  value: z.unknown(),
  updatedAt: isoDateSchema.optional(),
});
export const portableSnapshotSchema = z.strictObject({
  schemaVersion: z.literal(1),
  exportedAt: isoDateSchema,
  source: z.string(),
  tables: z.record(z.string(), z.array(z.record(z.string(), z.unknown()))),
  counts: z.record(z.string(), integer),
  checksum: z.string().regex(/^[a-f0-9]{64}$/),
});
export const encryptedEnvelopeSchema = z.strictObject({
  version: z.literal(1),
  algorithm: z.literal("PBKDF2-AES-GCM"),
  iterations: z.number().int().min(100000).max(2000000),
  salt: z.string().min(1),
  iv: z.string().min(1),
  ciphertext: z.string().min(1),
});
export const decryptedBackupSchema = z.strictObject({
  schemaVersion: z.literal(1),
  exportedAt: isoDateSchema,
  stores: z.record(z.string(), z.array(z.unknown())),
});
