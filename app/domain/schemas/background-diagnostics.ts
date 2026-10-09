import { z } from "zod";

export const backgroundKindSchema = z.enum(["canonical_prefix_preview", "coverage_explorer", "coverage_seed", "daily_queue", "daily_statistics", "defensive_admission", "defensive_rubric_audit", "defensive_threat_backfill", "defensive_threat_report_audit", "defensive_threat_scan", "defensive_threat_validate", "discovery_admission", "discovery_recommendation", "engine_defense", "engine_game", "game_analysis_followup", "game_analysis_publish", "game_derivation_compare", "game_derivation_events", "game_derivation_features", "game_derivation_findings", "game_derivation_misses", "game_derivation_positions", "game_derivation_priorities", "game_sync_record", "game_sync_window", "integrity_scan", "next_opponent_profile", "opening_graph_rebuild", "opening_segmentation", "other", "prefix_transition_application", "priority_retention", "repertoire_game_refresh", "repertoire_opportunity", "repertoire_priority"]);
const nonnegative = z.number().finite().nonnegative();
const timestamp = z.iso.datetime({ offset: true });
export const engineAttemptDiagnosticsSchema = z.object({
  outcome: z.enum(["success", "preempted", "timeout", "failure"]),
  elapsed_seconds: nonnegative.max(3600),
}).strict();
const counts = z.object({
  generations_started: nonnegative.int().default(0),
  generation_replacements: nonnegative.int().default(0),
  generation_restarts: nonnegative.int().default(0),
  claims: nonnegative.int().default(0),
  slices: nonnegative.int().default(0),
  retries: nonnegative.int().default(0),
  completed_generations: nonnegative.int().default(0),
  useful_completions: nonnegative.int().default(0),
  stale_deliveries: nonnegative.int().default(0),
  stale_results: nonnegative.int().default(0),
  lease_expiries: nonnegative.int().default(0),
  lease_reclaims: nonnegative.int().default(0),
  contention_deferrals: nonnegative.int().default(0),
  engine_completed_positions: nonnegative.int().default(0),
  engine_preemptions: nonnegative.int().default(0),
  engine_timeouts: nonnegative.int().default(0),
  engine_failures: nonnegative.int().default(0),
  priority_calculator_calls: nonnegative.int().default(0),
  priority_publications: nonnegative.int().default(0),
  priority_published_records: nonnegative.int().default(0),
  engine_successful_seconds: nonnegative.default(0),
  engine_preempted_seconds: nonnegative.default(0),
  engine_abandoned_seconds: nonnegative.default(0),
  engine_successful_samples: nonnegative.int().default(0),
  engine_abandoned_samples: nonnegative.int().default(0),
  engine_unknown_timing_attempts: nonnegative.int().default(0),
  engine_successful_max_seconds: nonnegative.default(0),
  engine_preempted_max_seconds: nonnegative.default(0),
  engine_abandoned_max_seconds: nonnegative.default(0),
}).strict();
const worker = z.object({
  kind: backgroundKindSchema, stage: z.enum(["idle", "dispatch", "foreground_admission", "database", "execution", "unknown"]),
  observed_at: timestamp, process_started_at: timestamp, admission_wait_seconds: nonnegative,
  handler_elapsed_seconds: nonnegative, execution_seconds: nonnegative,
  dispatch_wait_seconds: nonnegative.nullable().default(null),
}).strict();
export const backgroundDiagnosticsSchema = z.object({
  collection_started_at: timestamp.nullable().default(null),
  schema_version: z.literal(1).default(1), generated_at: timestamp, window_start: timestamp, window_end: timestamp,
  bucket_seconds: z.literal(300).default(300), retention_seconds: z.literal(86400).default(86400),
  query_duration_seconds: nonnegative, available: z.boolean(),
  unavailable_reason: z.enum(["query_deadline", "storage_unavailable"]).nullable().default(null),
  queues: z.array(z.object({
    queue: z.enum(["durable", "engine_game", "engine_defense"]),
    underlying_state: z.enum(["queued", "retrying", "delayed", "paused", "leased", "failed", "complete", "blocked", "superseded", "publishing"]).nullable().default(null),
    state: z.enum(["queued", "retrying", "delayed", "paused", "leased", "failed", "complete", "blocked", "superseded", "publishing"]),
    count: nonnegative.int(), oldest_pending_age_seconds: nonnegative.nullable().default(null),
    oldest_generation_age_seconds: nonnegative.nullable().default(null),
    next_eligibility_seconds: nonnegative.nullable().default(null), estimated_age_count: nonnegative.int().default(0),
  }).strict()).max(27).default([]),
  counters: z.array(z.object({ kind: backgroundKindSchema, counts,
    useful_completion_unit: z.enum(["accepted_position", "published_priority_generation", "published_game_analysis"]).nullable().default(null),
  }).strict()).max(backgroundKindSchema.options.length).default([]),
  runtime: z.object({ available: z.boolean().default(false), retention_seconds: z.literal(15).default(15),
    coverage: z.literal("fixed_slots_latest_samples").default("fixed_slots_latest_samples"),
    workers: z.array(worker).max(16).default([]),
  }).strict(),
}).strict();
export type BackgroundDiagnostics = z.infer<typeof backgroundDiagnosticsSchema>;
