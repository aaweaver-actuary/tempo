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

export const GameWindowSchema = z.strictObject({ start_at: utc, end_at: utc, precision }).refine(value => Date.parse(value.start_at) < Date.parse(value.end_at), "window must be nonempty");
export const GameRecordSchema = z.strictObject({ source_game_id: id, source_revision: revision, source_digest: digest, pgn: z.string().min(1), played_at: utc.nullable(), time_precision: precision }).refine(value => (value.played_at === null) === (value.time_precision === "unknown"), "unknown source time remains null");
export const GamePageSchema = z.strictObject({ interface_version: version, provider, account: id, window: GameWindowSchema, records: z.array(GameRecordSchema), rejections: z.array(z.strictObject({ source_game_id: id.nullable(), source_revision: revision.nullable(), reason: id })), continuation: z.string().nullable(), complete: z.boolean(), saturated: z.boolean() }).superRefine((value, ctx) => {
  if (value.complete && (value.saturated || value.continuation !== null)) ctx.addIssue({ code: "custom", message: "saturated/continuing page cannot be complete" });
  if (new Set(value.records.map(item => item.source_game_id)).size !== value.records.length) ctx.addIssue({ code: "custom", message: "conflicting game revisions in page" });
});

export const FindingSchema = z.strictObject({
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
export const DecisionPresentationSchema = z.strictObject({ decision_id: id, repertoire_id: id, position_key: id, selected_response_uci: uci, presentation_id: id, presentation_revision: revision, repertoire_revision: revision, introduction: z.enum(["introduced", "unintroduced", "unknown"]), introduced_at: utc.nullable() }).refine(value => value.introduction === "introduced" || value.introduced_at === null, "unknown/unintroduced cannot assert introduction time");
export const RepertoireSnapshotSchema = z.strictObject({ interface_version: version, membership: MembershipSnapshotSchema, variant: id, learner_color: z.enum(["white", "black"]), boundaries: z.array(z.strictObject({ route_id: id, terminal_ply: z.number().int().min(0), deliberate: z.boolean() })), decision_presentations: z.array(DecisionPresentationSchema) }).superRefine((value, ctx) => {
  const routes = new Map(value.membership.routes.map(route => [route.route_id, route]));
  if (routes.size !== value.membership.routes.length || value.boundaries.length !== routes.size || new Set(value.boundaries.map(item => item.route_id)).size !== routes.size || value.boundaries.some(item => routes.get(item.route_id)?.moves.length !== item.terminal_ply)) ctx.addIssue({ code: "custom", message: "every route requires its actual terminal boundary" });
  if (value.decision_presentations.some(item => item.repertoire_revision !== value.membership.revision || !value.membership.scope_repertoire_ids.includes(item.repertoire_id))) ctx.addIssue({ code: "custom", message: "conflicting repertoire revision/scope" });
});

export const GameEffectKeySchema = z.strictObject({ workspace_id: id, provider, game_id: id, decision_id: id, occurrence_ply: z.number().int().min(1).max(1000) });
export const GameLapseSchema = z.strictObject({ key: GameEffectKeySchema, finding: FindingSchema, detected_at: utc, occurred_at: utc.nullable(), payload_digest: digest }).refine(value => value.finding.kind === "confirmed_miss" && value.key.provider === value.finding.provider && value.key.game_id === value.finding.game_id && value.key.decision_id === value.finding.decision_id && value.key.occurrence_ply === value.finding.ply, "lapse must name same confirmed occurrence");
// Exact v2 EvidenceWithdrawal wire shape; v3 already supports withdrawal-only envelopes.
export const EvidenceWithdrawalSchema = z.strictObject({ obligation_id: id, expected_obligation_revision: revision, reason: z.enum(["source_corrected", "source_excluded", "source_deleted", "reanalysis"]) });
export const GameEffectControlSchema = z.strictObject({ key: GameEffectKeySchema, source_revision: revision, action: z.enum(["hold", "withdraw"]), withdrawal: EvidenceWithdrawalSchema });
export const LearningReceiptSchema = z.strictObject({ key: GameEffectKeySchema, source_revision: revision, status: z.enum(["pending", "held", "applied", "settled"]), outcome: z.enum(["accepted", "duplicate", "conflict", "stale_revision", "withdrawn", "historical_preserved"]), owner_receipt_id: id, admitted_at: utc.nullable(), available_by: utc.nullable(), applied_at: utc.nullable(), reason: id.nullable() });

export type GamePage = z.infer<typeof GamePageSchema>;
export type Finding = z.infer<typeof FindingSchema>;
export type RepertoireSnapshot = z.infer<typeof RepertoireSnapshotSchema>;
export type GameEffectKey = z.infer<typeof GameEffectKeySchema>;
export type GameLapse = z.infer<typeof GameLapseSchema>;
export type GameEffectControl = z.infer<typeof GameEffectControlSchema>;
export type LearningReceipt = z.infer<typeof LearningReceiptSchema>;
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
  findings(gameId: string): Promise<readonly Finding[]>;
  feedbackStatus(key: GameEffectKey): Promise<LearningReceipt | null>;
}

export const PRODUCT_SCHEMAS = { GamePage: GamePageSchema, Finding: FindingSchema, RepertoireSnapshot: RepertoireSnapshotSchema, GameLapse: GameLapseSchema, GameEffectControl: GameEffectControlSchema, LearningReceipt: LearningReceiptSchema } as const;
