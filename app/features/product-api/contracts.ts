/** Product v0.2 wire values. No fetch, stores, scheduling or component imports. */
import { z } from "zod";

export const INTERFACE_VERSION = "0.2.0";
const id = z.string().min(1).max(200);
const revision = z.number().int().min(1).max(Number.MAX_SAFE_INTEGER);
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const uci = z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/);
const utc = z.iso.datetime({ offset: true }).refine(value => value.endsWith("Z") || value.endsWith("+00:00"), "explicit UTC required");
const provider = z.enum(["lichess", "chesscom"]);
const precision = z.enum(["unknown", "day", "hour", "minute", "second", "millisecond"]);
const version = z.literal(INTERFACE_VERSION).default(INTERFACE_VERSION);

const RawGameWindowSchema = z.strictObject({ start_at: utc, end_at: utc, precision }).refine(value => Date.parse(value.start_at) < Date.parse(value.end_at), "window must be nonempty");
const RawGameRecordSchema = z.strictObject({ source_game_id: id, source_revision: revision, source_digest: digest, pgn: z.string().min(1), played_at: utc.nullable(), time_precision: precision }).refine(value => (value.played_at === null) === (value.time_precision === "unknown"), "unknown source time remains null");
const RawGamePageSchema = z.strictObject({ interface_version: version, provider, account: id, window: RawGameWindowSchema, records: z.array(RawGameRecordSchema), rejections: z.array(z.strictObject({ source_game_id: id.nullable(), source_revision: revision.nullable(), reason: id })), continuation: z.string().nullable(), complete: z.boolean(), saturated: z.boolean(), filters: z.array(z.tuple([z.string(), z.string()])).default([]) }).superRefine((value, ctx) => {
  if (value.complete && (value.saturated || value.continuation !== null)) ctx.addIssue({ code: "custom", message: "saturated/continuing page cannot be complete" });
  if (new Set(value.records.map(item => item.source_game_id)).size !== value.records.length) ctx.addIssue({ code: "custom", message: "conflicting game revisions in page" });
});

const RawFindingSchema = z.strictObject({
  interface_version: version,
  kind: z.enum(["confirmed_miss", "opponent_gap", "missing_own_continuation", "known_unintroduced", "known_unavailable", "conflict", "ambiguous", "coverage_end"]),
  provider, game_id: id, source_revision: revision, source_digest: digest, ply: z.number().int().min(1).max(1000), position_key: id, expected_uci: z.array(uci), played_uci: uci,
  repertoire_id: id, repertoire_revision: revision, decision_id: id.nullable(), presentation_id: id.nullable(), presentation_revision: revision.nullable(), introduced_before_game: z.boolean().nullable(),
  membership: z.enum(["present", "absent", "conflict", "unknown"]), lapse_eligible: z.boolean(), diagnostic: id.nullable(), reentry_authority_id: id.nullable().default(null),
}).superRefine((value, ctx) => {
  if (value.kind === "confirmed_miss") {
    if (!(value.decision_id && value.presentation_id && value.presentation_revision && value.introduced_before_game === true && value.membership === "present" && value.lapse_eligible && value.expected_uci.length === 1 && !value.expected_uci.includes(value.played_uci))) ctx.addIssue({ code: "custom", message: "confirmed miss needs introduced mapped decision and one different own response" });
  } else if (value.lapse_eligible || value.diagnostic === null) ctx.addIssue({ code: "custom", message: "non-miss needs diagnostic and no lapse eligibility" });
  if ((value.presentation_id === null) !== (value.presentation_revision === null)) ctx.addIssue({ code: "custom", message: "presentation identity requires revision" });
});

const ReentrySchema = z.strictObject({ ply: z.number().int().min(0).max(1000), authority_id: id });
const AuthoredRouteSchema = z.strictObject({ repertoire_id: id, route_id: id, policy_id: id, learner_color: z.enum(["white", "black"]), starting_fen: id, moves: z.array(uci).max(1000), reentries: z.array(ReentrySchema).max(1000).default([]) });
// Wire mirror of tempo_game_review_contracts.RepertoireSnapshot; Python reuses that owner.
const MembershipSnapshotSchema = z.strictObject({ schema_version: z.literal(1).default(1), workspace_id: id, snapshot_id: id, revision, digest, source_revision: id, captured_at: utc, basis: z.literal("current_repertoire").default("current_repertoire"), scope_id: id, scope_repertoire_ids: z.array(id).min(1), complete: z.boolean(), available: z.boolean().default(true), unavailable_reason: id.nullable().default(null), routes: z.array(AuthoredRouteSchema) });
const IntroductionGameScopeSchema = z.strictObject({ provider, game_id: id, source_revision: revision, source_digest: digest });
const RawDecisionPresentationSchema = z.strictObject({ decision_id: id, repertoire_id: id, position_key: id, selected_response_uci: uci, presentation_id: id, presentation_revision: revision, repertoire_revision: revision, introduction: z.enum(["introduced", "unintroduced", "unknown"]), introduced_at: utc.nullable(), available: z.boolean().default(false), introduction_matches_current_content: z.boolean().default(false), route_id: id.nullable().default(null), route_ply: z.number().int().min(0).max(1000).nullable().default(null), introduced_before_game: z.boolean().nullable().default(null), introduction_game: IntroductionGameScopeSchema.nullable().default(null) }).superRefine((value, ctx) => {
  if (value.introduction !== "introduced" && value.introduced_at !== null) ctx.addIssue({ code: "custom", message: "unknown/unintroduced cannot assert introduction time" });
  if ((value.route_id === null) !== (value.route_ply === null)) ctx.addIssue({ code: "custom", message: "route identity/zero-based ply must be paired" });
  if ((value.introduced_before_game === null) !== (value.introduction_game === null)) ctx.addIssue({ code: "custom", message: "chronology boolean requires exact captured game source" });
  if (value.introduced_before_game === true && value.introduction !== "introduced") ctx.addIssue({ code: "custom", message: "pre-game introduction cannot be unknown/unintroduced" });
});
const RawRepertoireSnapshotSchema = z.strictObject({ interface_version: version, membership: MembershipSnapshotSchema, variant: id, learner_color: z.enum(["white", "black"]), boundaries: z.array(z.strictObject({ route_id: id, terminal_ply: z.number().int().min(0), deliberate: z.boolean() })), decision_presentations: z.array(RawDecisionPresentationSchema) }).superRefine((value, ctx) => {
  const routes = new Map(value.membership.routes.map(route => [route.route_id, route]));
  if (routes.size !== value.membership.routes.length || value.boundaries.length !== routes.size || new Set(value.boundaries.map(item => item.route_id)).size !== routes.size || value.boundaries.some(item => routes.get(item.route_id)?.moves.length !== item.terminal_ply)) ctx.addIssue({ code: "custom", message: "every route requires its actual terminal boundary" });
  if (value.decision_presentations.some(item => item.repertoire_revision !== value.membership.revision || !value.membership.scope_repertoire_ids.includes(item.repertoire_id))) ctx.addIssue({ code: "custom", message: "conflicting repertoire revision/scope" });
  for (const item of value.decision_presentations) {
    if (item.route_id !== null) {
      const route = routes.get(item.route_id);
      if (!route || route.repertoire_id !== item.repertoire_id || item.route_ply === null || item.route_ply >= route.moves.length || route.moves[item.route_ply] !== item.selected_response_uci) ctx.addIssue({ code: "custom", message: "route-local mapping must identify the authored response occurrence" });
    }
  }
});

const RawGameEffectKeySchema = z.strictObject({ workspace_id: id, provider, game_id: id, decision_id: id, occurrence_ply: z.number().int().min(1).max(1000) });
const RawGameLapseSchema = z.strictObject({ key: RawGameEffectKeySchema, finding: RawFindingSchema, detected_at: utc, occurred_at: utc.nullable(), payload_digest: digest }).refine(value => value.finding.kind === "confirmed_miss" && value.key.provider === value.finding.provider && value.key.game_id === value.finding.game_id && value.key.decision_id === value.finding.decision_id && value.key.occurrence_ply === value.finding.ply, "lapse must name same confirmed occurrence");
// Exact v2 EvidenceWithdrawal wire shape; v3 already supports withdrawal-only envelopes.
const RawEvidenceWithdrawalSchema = z.strictObject({ obligation_id: id, expected_obligation_revision: revision, reason: z.enum(["source_corrected", "source_excluded", "source_deleted", "reanalysis"]) });
const controlReason = z.enum(["source_corrected", "source_excluded", "source_deleted", "reanalysis"]);
const RawGameEffectControlSchema = z.strictObject({ key: RawGameEffectKeySchema, source_revision: revision, action: z.enum(["hold", "withdraw"]), reason: controlReason.nullable().default(null), withdrawal: RawEvidenceWithdrawalSchema.nullable().default(null) }).refine(value => (value.reason !== null || value.withdrawal !== null) && !(value.reason !== null && value.withdrawal !== null && value.reason !== value.withdrawal.reason), "control requires a reason; optional existing withdrawal must agree");
const RawLearningReceiptSchema = z.strictObject({ key: RawGameEffectKeySchema, source_revision: revision, status: z.enum(["pending", "held", "applied", "settled"]), outcome: z.enum(["accepted", "duplicate", "conflict", "stale_revision", "withdrawn", "historical_preserved"]), owner_receipt_id: id, admitted_at: utc.nullable(), available_by: utc.nullable(), applied_at: utc.nullable(), reason: id.nullable() });
// Wire mirror of the existing frozen learning.EncounterReceipt; no new effect type.
const RawEncounterReceiptSchema = RawLearningReceiptSchema.safeExtend({ interface_version: z.literal("0.2.1").default("0.2.1"), detected_at: utc, source_digest: digest, observation: z.enum(["matched", "missed"]), observation_recorded: z.boolean(), disposition: z.enum(["review_priority", "first_introduction", "existing_lapse", "frequency_only"]).nullable(), admission_status: z.enum(["pending", "available", "settled", "not_requested"]), deferred_reason: z.enum(["study_only", "disabled", "paused", "zero_limit", "introduction_allowance", "active_attempt", "authority_unavailable", "unknown_chronology"]).nullable() }).superRefine((value, ctx) => {
  if (value.observation === "matched" && value.disposition === "existing_lapse") ctx.addIssue({ code: "custom", message: "correct play cannot produce a lapse" });
  if (value.disposition === "frequency_only" && (value.admission_status !== "not_requested" || value.observation !== "matched")) ctx.addIssue({ code: "custom", message: "frequency-only cannot claim admission or discard missed-response importance" });
  if (value.admission_status === "available" && (value.disposition === null || value.status === "held")) ctx.addIssue({ code: "custom", message: "unresolved/held evidence cannot claim availability" });
  if (value.deferred_reason !== null && value.admission_status !== "pending") ctx.addIssue({ code: "custom", message: "deferred work remains pending" });
  if (value.admission_status === "pending" && value.deferred_reason === null && value.reason === null) ctx.addIssue({ code: "custom", message: "pending admission requires an owner reason" });
  if (value.disposition === "first_introduction" && ["study_only", "zero_limit", "introduction_allowance"].includes(value.deferred_reason ?? "")) ctx.addIssue({ code: "custom", message: "encounter first introduction uses the targeted exception" });
  if (["disabled", "paused"].includes(value.deferred_reason ?? "") && value.available_by !== null) ctx.addIssue({ code: "custom", message: "full-pause controls cannot promise an availability deadline" });
});


export type DeepReadonly<T> = T extends object ? { readonly [Key in keyof T]: DeepReadonly<T[Key]> } : T;
function freezeValue<T>(value: T): DeepReadonly<T> {
  if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) freezeValue(item);
    Object.freeze(value);
  }
  return value as DeepReadonly<T>;
}
function immutable<Schema extends z.ZodType>(schema: Schema) {
  return schema.transform(value => freezeValue(value));
}
export const GameWindowSchema = immutable(RawGameWindowSchema);
export const GameRecordSchema = immutable(RawGameRecordSchema);
export const GamePageSchema = immutable(RawGamePageSchema);
export const FindingSchema = immutable(RawFindingSchema);
export const DecisionPresentationSchema = immutable(RawDecisionPresentationSchema);
export const RepertoireSnapshotSchema = immutable(RawRepertoireSnapshotSchema);
export const GameEffectKeySchema = immutable(RawGameEffectKeySchema);
export const GameLapseSchema = immutable(RawGameLapseSchema);
export const EvidenceWithdrawalSchema = immutable(RawEvidenceWithdrawalSchema);
export const GameEffectControlSchema = immutable(RawGameEffectControlSchema);
export const LearningReceiptSchema = immutable(RawLearningReceiptSchema);
export const EncounterReceiptSchema = immutable(RawEncounterReceiptSchema);

export type GamePage = z.infer<typeof GamePageSchema>;
export type Finding = z.infer<typeof FindingSchema>;
export type RepertoireSnapshot = z.infer<typeof RepertoireSnapshotSchema>;
export type GameEffectKey = z.infer<typeof GameEffectKeySchema>;
export type GameLapse = z.infer<typeof GameLapseSchema>;
export type GameEffectControl = z.infer<typeof GameEffectControlSchema>;
export type LearningReceipt = z.infer<typeof LearningReceiptSchema>;
export type EncounterReceipt = z.infer<typeof EncounterReceiptSchema>;
export function logicalEffectId(key: GameEffectKey): string {
  return JSON.stringify([key.workspace_id, key.provider, key.game_id, key.decision_id, key.occurrence_ply]);
}

export interface LearningFeedbackPort {
  acceptLapse(lapse: GameLapse): Promise<LearningReceipt>;
  controlEffect(command: GameEffectControl): Promise<LearningReceipt>;
  effectStatus(key: GameEffectKey): Promise<LearningReceipt | null>;
}

/** Read/query screen boundary only; Repertoire/Study owners apply commands. */
export interface GameReviewApi {
  recentGames(request?: { signal?: AbortSignal }): Promise<RecentGames>;
  gameDetail(request: { provider: GameSummary["provider"]; game_id: string; repertoire_id?: string; signal?: AbortSignal }): Promise<GameDetail>;
  sync(request: { provider: GameSummary["provider"]; operation_id: string; signal?: AbortSignal }): Promise<ProviderSyncStatus>;
  retryFeedback(request: { finding: Finding; operation_id: string; signal?: AbortSignal }): Promise<LearningReceipt>;
  findings(gameId: string): Promise<readonly Finding[]>;
  feedbackStatus(key: GameEffectKey): Promise<LearningReceipt | null>;
}

const RawGameSummarySchema = z.strictObject({ provider, game_id: id, source_revision: revision, played_at: utc.nullable(), time_precision: precision, player_color: z.enum(["white", "black"]) }).refine(value => (value.played_at === null) === (value.time_precision === "unknown"), "preserve unknown source time");
const RawProviderSyncStatusSchema = z.strictObject({ provider, status: z.enum(["idle", "pending", "complete", "failed"]), operation_id: id.nullable(), error: id.nullable() }).refine(value => (value.status === "failed") === (value.error !== null) && (value.status === "idle" || value.operation_id !== null), "status must preserve failure and operation identity");
const RawRecentGamesSchema = z.strictObject({ games: z.array(RawGameSummarySchema), sync: z.array(RawProviderSyncStatusSchema) });
const RawGameDetailSchema = RawGameSummarySchema.safeExtend({ starting_fen: id, moves: z.array(uci).max(1000), findings: z.array(RawFindingSchema), receipts: z.array(z.union([RawEncounterReceiptSchema, RawLearningReceiptSchema])) });
export const GameSummarySchema = immutable(RawGameSummarySchema);
export const ProviderSyncStatusSchema = immutable(RawProviderSyncStatusSchema);
export const RecentGamesSchema = immutable(RawRecentGamesSchema);
export const GameDetailSchema = immutable(RawGameDetailSchema);
export type GameSummary = z.infer<typeof GameSummarySchema>;
export type ProviderSyncStatus = z.infer<typeof ProviderSyncStatusSchema>;
export type RecentGames = z.infer<typeof RecentGamesSchema>;
export type GameDetail = z.infer<typeof GameDetailSchema>;

export const PRODUCT_SCHEMAS = { GamePage: GamePageSchema, Finding: FindingSchema, RepertoireSnapshot: RepertoireSnapshotSchema, GameLapse: GameLapseSchema, GameEffectControl: GameEffectControlSchema, LearningReceipt: LearningReceiptSchema, EncounterReceipt: EncounterReceiptSchema, GameSummary: GameSummarySchema, ProviderSyncStatus: ProviderSyncStatusSchema, RecentGames: RecentGamesSchema, GameDetail: GameDetailSchema } as const;
