/** Product v0.2 wire values. No fetch, stores, scheduling or component imports. */
import { z } from "zod";
import { Chess } from "chess.js";

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

const RawFirstDepartureScopeSchema = z.strictObject({ repertoire_id: id, learner_color: z.enum(["white", "black"]).nullable(), snapshot_id: id.nullable(), source_revision: id.nullable(), revision: revision.nullable(), digest: digest.nullable(), complete: z.boolean(), available: z.boolean() }).superRefine((value, ctx) => {
  if (value.available && [value.learner_color, value.snapshot_id, value.source_revision, value.revision, value.digest].some(item => item === null)) ctx.addIssue({ code: "custom", message: "available selected scope requires actual snapshot identity" });
  if (value.complete && !value.available) ctx.addIssue({ code: "custom", message: "unavailable scope cannot claim completeness" });
});
const RawFirstDepartureRouteSchema = z.strictObject({ repertoire_id: id, route_id: id, route_ply: z.number().int().min(0).max(1000), policy_id: id, source_revision: id, at_deliberate_end: z.boolean() });
const RawFirstDepartureSelectionSchema = z.strictObject({ policy_version: z.literal("first_departure_v1").default("first_departure_v1"), workspace_id: id, source: IntroductionGameScopeSchema, learner_color: z.enum(["white", "black"]), selected_scope: z.array(RawFirstDepartureScopeSchema).max(10000), status: z.enum(["covered", "own_actionable", "own_unmapped", "opponent_gap", "coverage_end", "missing_own_continuation", "unresolved"]), ply: z.number().int().min(1).max(1000).nullable(), actor: z.enum(["learner", "opponent"]).nullable(), position_key: id.nullable(), played_uci: uci.nullable(), expected_uci: z.array(uci), decision: RawDecisionPresentationSchema.nullable(), surviving_routes: z.array(RawFirstDepartureRouteSchema), diagnostic: id.nullable() }).superRefine((value, ctx) => {
  const fail = (message: string) => ctx.addIssue({ code: "custom", message });
  const scopes = new Map(value.selected_scope.map(item => [item.repertoire_id, item]));
  if (scopes.size !== value.selected_scope.length) fail("selected scope identities must be unique");
  if (value.selected_scope.some(item => item.learner_color !== null && item.learner_color !== value.learner_color)) fail("selected relevant scope must retain learner color");
  if (new Set(value.expected_uci).size !== value.expected_uci.length) fail("expected alternatives must be distinct");
  const facts = [value.ply, value.actor, value.position_key, value.played_uci];
  if (facts.some(item => item !== null) && !facts.every(item => item !== null)) fail("known boundary requires complete pre-move occurrence");
  if (value.status === "covered" && (facts.some(item => item !== null) || value.decision !== null || value.expected_uci.length)) fail("covered played moves cannot claim departure or target");
  if (value.status === "covered" && !value.selected_scope.length) fail("absent relevant scope is unresolved, not covered");
  if (!["covered", "unresolved"].includes(value.status) && value.ply === null) fail("selected boundary must retain occurrence");
  if (["own_actionable", "own_unmapped", "missing_own_continuation"].includes(value.status) && value.actor !== "learner") fail("learner boundary requires learner actor");
  if (value.status === "opponent_gap" && value.actor !== "opponent") fail("opponent gap cannot authorize learner effect");
  if (["own_actionable", "own_unmapped", "opponent_gap"].includes(value.status) && value.expected_uci.includes(value.played_uci!)) fail("departure cannot be a supported expected alternative");
  if (value.status === "coverage_end" && (value.expected_uci.length || !value.surviving_routes.length || !value.surviving_routes.every(item => item.at_deliberate_end))) fail("coverage end requires all surviving authored routes deliberately ended and no expected move");
  if (value.status === "missing_own_continuation" && value.expected_uci.length) fail("missing own continuation cannot invent an expected response");
  if (new Set(value.surviving_routes.map(item => JSON.stringify([item.repertoire_id, item.route_id, item.route_ply, item.policy_id]))).size !== value.surviving_routes.length) fail("surviving route occurrences must be distinct");
  if (value.surviving_routes.some(item => !scopes.has(item.repertoire_id) || scopes.get(item.repertoire_id)!.source_revision !== item.source_revision)) fail("route must bind selected source revision");
  if (value.status !== "own_actionable" && value.decision !== null) fail("non-actionable result cannot claim actionable target");
  if (["own_unmapped", "missing_own_continuation", "unresolved"].includes(value.status) && value.diagnostic === null) fail("non-actionable own boundary needs specific diagnostic");
  if (value.status === "own_actionable") {
    const decision = value.decision;
    if (decision === null || !decision.available || decision.route_id === null || value.expected_uci.length !== 1 || value.expected_uci.includes(value.played_uci!) || value.expected_uci[0] !== decision.selected_response_uci || value.position_key !== decision.position_key) fail("actionable departure requires exact available pre-move target");
    if (!value.selected_scope.length || value.selected_scope.some(item => !item.complete || !item.available)) fail("incomplete scope cannot authorize Study");
    if (decision !== null) {
      const scope = scopes.get(decision.repertoire_id);
      if (!scope || decision.repertoire_revision !== scope.revision) fail("target must bind selected repertoire revision");
      if (!value.surviving_routes.length || value.surviving_routes.some(item => item.at_deliberate_end) || !value.surviving_routes.some(item => item.repertoire_id === decision.repertoire_id && item.route_id === decision.route_id && item.route_ply === decision.route_ply)) fail("target needs surviving occurrence without ended competitor");
    }
  }
});
const RawFirstDepartureWitnessSchema = z.strictObject({ selection: RawFirstDepartureSelectionSchema, configuration_revision: revision, determined_at: utc, stream: z.enum(["forward", "reconciliation", "manual"]) }).refine(value => value.selection.status === "own_actionable", "only exact learner first departure can qualify Study intake");
const RawEncounterTargetSchema = z.strictObject({ card_id: id, card_revision: revision });
const RawIssuedAttemptEvidenceSchema = z.strictObject({ queue_entry_id: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER), card_id: id, card_revision: revision, issuance_sequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER), issued_at: utc });
const RawRemediationFenceSchema = z.strictObject({ target: RawEncounterTargetSchema, after_issuance_sequence: z.number().int().min(0).max(Number.MAX_SAFE_INTEGER), established_at: utc });
const RawEncounterSettlementSchema = z.strictObject({ review_id: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER), attempt: RawIssuedAttemptEvidenceSchema });
// PostgreSQL/Python preserve microseconds; Date.parse alone truncates them.
function compareUtc(left: string, right: string): number {
  const parts = (value: string) => {
    const fraction = /\.(\d+)/.exec(value)?.[1] ?? "";
    return [Date.parse(value.replace(/\.\d+(?=Z$|\+00:00$)/, "")), Number((fraction + "000000").slice(0, 6))];
  };
  const a = parts(left), b = parts(right);
  return a[0] - b[0] || a[1] - b[1];
}
function followsFence(fence: z.infer<typeof RawRemediationFenceSchema>, attempt: z.infer<typeof RawIssuedAttemptEvidenceSchema>) {
  return attempt.card_id === fence.target.card_id && attempt.card_revision === fence.target.card_revision && attempt.issuance_sequence > fence.after_issuance_sequence && compareUtc(attempt.issued_at, fence.established_at) >= 0;
}
const RawEncounterProgressSchema = z.strictObject({ policy_version: z.literal("first_departure_v1").default("first_departure_v1"), determined_at: utc, stream: z.enum(["forward", "reconciliation", "manual"]), target: RawEncounterTargetSchema, scheduling_review_id: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER).nullable(), schedule_applied_at: utc.nullable(), introduction_applied_at: utc.nullable(), remediation_fence: RawRemediationFenceSchema.nullable(), first_issued: RawIssuedAttemptEvidenceSchema.nullable(), first_issued_ordinal: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER).nullable(), settlement: RawEncounterSettlementSchema.nullable() }).superRefine((value, ctx) => {
  const fail = (message: string) => ctx.addIssue({ code: "custom", message });
  if ((value.scheduling_review_id === null) !== (value.schedule_applied_at === null)) fail("Again needs actual scheduling review and application time");
  if (value.schedule_applied_at !== null && value.introduction_applied_at !== null) fail("one disposition cannot claim Again and introduction");
  const application = value.schedule_applied_at ?? value.introduction_applied_at;
  const fence = value.remediation_fence;
  if ((fence === null) !== (application === null)) fail("application/admission needs issuance fence");
  if (fence !== null && (fence.target.card_id !== value.target.card_id || fence.target.card_revision !== value.target.card_revision || application === null || compareUtc(fence.established_at, application) < 0)) fail("fence binds actual target at or after application boundary");
  if ((value.first_issued === null) !== (value.first_issued_ordinal === null)) fail("first issuance and measured ordinal must be paired");
  if (value.first_issued !== null && (fence === null || !followsFence(fence, value.first_issued))) fail("remedial issuance must follow application");
  if (value.first_issued !== null && compareUtc(value.first_issued.issued_at, value.determined_at) < 0) fail("target issuance cannot precede original determination");
  if (value.settlement !== null && (value.first_issued === null || fence === null || !followsFence(fence, value.settlement.attempt) || value.settlement.attempt.issuance_sequence < value.first_issued.issuance_sequence)) fail("settlement requires compatible post-application issued attempt");
  if (value.settlement !== null && value.first_issued !== null) {
    const first = value.first_issued, completed = value.settlement.attempt;
    const sameGeneration = first.queue_entry_id === completed.queue_entry_id && first.card_id === completed.card_id && first.card_revision === completed.card_revision;
    const sameSequence = first.issuance_sequence === completed.issuance_sequence;
    if ((sameSequence && !sameGeneration) || (sameGeneration && (!sameSequence || compareUtc(first.issued_at, completed.issued_at) !== 0))) fail("once-stamped issuance retains actual generation, sequence and time");
  }
});

const RawGameEffectKeySchema = z.strictObject({ workspace_id: id, provider, game_id: id, decision_id: id, occurrence_ply: z.number().int().min(1).max(1000) });
const RawGameEncounterSchema = z.strictObject({ interface_version: z.literal("0.2.1").default("0.2.1"), key: RawGameEffectKeySchema, source: IntroductionGameScopeSchema, decision: RawDecisionPresentationSchema, expected_uci: uci, played_uci: uci, observation: z.enum(["matched", "missed"]), detected_at: utc, occurred_at: utc.nullable(), payload_digest: digest }).superRefine((value, ctx) => {
  if (value.key.provider !== value.source.provider || value.key.game_id !== value.source.game_id || value.key.decision_id !== value.decision.decision_id) ctx.addIssue({ code: "custom", message: "encounter key must identify same provider/game/decision" });
  if (value.expected_uci !== value.decision.selected_response_uci || (value.observation === "matched") !== (value.played_uci === value.expected_uci)) ctx.addIssue({ code: "custom", message: "encounter preserves expected/played facts" });
  const chronology = value.decision.introduction_game;
  if (chronology !== null && (chronology.provider !== value.source.provider || chronology.game_id !== value.source.game_id || chronology.source_revision !== value.source.source_revision || chronology.source_digest !== value.source.source_digest)) ctx.addIssue({ code: "custom", message: "supplied chronology must identify exact source" });
});
const RawGameLapseSchema = z.strictObject({ key: RawGameEffectKeySchema, finding: RawFindingSchema, detected_at: utc, occurred_at: utc.nullable(), payload_digest: digest }).refine(value => value.finding.kind === "confirmed_miss" && value.key.provider === value.finding.provider && value.key.game_id === value.finding.game_id && value.key.decision_id === value.finding.decision_id && value.key.occurrence_ply === value.finding.ply, "lapse must name same confirmed occurrence");
// Exact v2 EvidenceWithdrawal wire shape; v3 already supports withdrawal-only envelopes.
const RawEvidenceWithdrawalSchema = z.strictObject({ obligation_id: id, expected_obligation_revision: revision, reason: z.enum(["source_corrected", "source_excluded", "source_deleted", "reanalysis"]) });
const controlReason = z.enum(["source_corrected", "source_excluded", "source_deleted", "reanalysis"]);
const RawGameEffectControlSchema = z.strictObject({ key: RawGameEffectKeySchema, source_revision: revision, action: z.enum(["hold", "withdraw"]), reason: controlReason.nullable().default(null), withdrawal: RawEvidenceWithdrawalSchema.nullable().default(null) }).refine(value => (value.reason !== null || value.withdrawal !== null) && !(value.reason !== null && value.withdrawal !== null && value.reason !== value.withdrawal.reason), "control requires a reason; optional existing withdrawal must agree");
const RawLearningReceiptSchema = z.strictObject({ key: RawGameEffectKeySchema, source_revision: revision, status: z.enum(["pending", "held", "applied", "settled"]), outcome: z.enum(["accepted", "duplicate", "conflict", "stale_revision", "withdrawn", "historical_preserved"]), owner_receipt_id: id, admitted_at: utc.nullable(), available_by: utc.nullable(), applied_at: utc.nullable(), reason: id.nullable() });

// Explicit 0.2.2 progress; stored 0.2.1 receipts remain readable without relabelling priority as Again.
const RawEncounterReceiptSchema = RawLearningReceiptSchema.safeExtend({ interface_version: z.enum(["0.2.1", "0.2.2"]).default("0.2.1"), detected_at: utc, source_digest: digest, observation: z.enum(["matched", "missed"]), observation_recorded: z.boolean(), disposition: z.enum(["review_priority", "first_introduction", "existing_lapse", "frequency_only", "introduced_again"]).nullable(), admission_status: z.enum(["pending", "available", "settled", "not_requested"]), deferred_reason: z.enum(["study_only", "disabled", "paused", "zero_limit", "introduction_allowance", "active_attempt", "authority_unavailable", "unknown_chronology"]).nullable(), progress: RawEncounterProgressSchema.optional() }).superRefine((value, ctx) => {
  if (value.observation === "matched" && value.disposition === "existing_lapse") ctx.addIssue({ code: "custom", message: "correct play cannot produce a lapse" });
  if (value.disposition === "frequency_only" && (value.admission_status !== "not_requested" || value.observation !== "matched")) ctx.addIssue({ code: "custom", message: "frequency-only cannot claim admission or discard missed-response importance" });
  if (value.admission_status === "available" && (value.disposition === null || value.status === "held")) ctx.addIssue({ code: "custom", message: "unresolved/held evidence cannot claim availability" });
  if (value.deferred_reason !== null && value.admission_status !== "pending") ctx.addIssue({ code: "custom", message: "deferred work remains pending" });
  if (value.admission_status === "pending" && value.deferred_reason === null && value.reason === null) ctx.addIssue({ code: "custom", message: "pending admission requires an owner reason" });
  if (value.disposition === "first_introduction" && ["study_only", "zero_limit", "introduction_allowance"].includes(value.deferred_reason ?? "")) ctx.addIssue({ code: "custom", message: "encounter first introduction uses the targeted exception" });
  if (["disabled", "paused"].includes(value.deferred_reason ?? "") && value.available_by !== null) ctx.addIssue({ code: "custom", message: "full-pause controls cannot promise an availability deadline" });
  if (value.interface_version === "0.2.1") {
    if (value.progress !== undefined || value.disposition === "introduced_again") ctx.addIssue({ code: "custom", message: "first-departure application evidence requires receipt0.2.2" });
    return;
  }
  const progress = value.progress;
  if (progress === undefined || !["introduced_again", "first_introduction"].includes(value.disposition ?? "") || value.observation !== "missed") {
    ctx.addIssue({ code: "custom", message: "0.2.2 needs qualified missed-departure progress and disposition" });
    return;
  }
  if (value.disposition === "introduced_again" && progress.introduction_applied_at !== null) ctx.addIssue({ code: "custom", message: "introduced Again cannot claim first introduction" });
  if (value.disposition === "first_introduction" && progress.scheduling_review_id !== null) ctx.addIssue({ code: "custom", message: "first introduction cannot fabricate Again" });
  if (["available", "settled"].includes(value.admission_status) && ((value.disposition === "introduced_again" ? progress.schedule_applied_at : progress.introduction_applied_at) === null || progress.remediation_fence === null)) ctx.addIssue({ code: "custom", message: "available consequence needs actual scheduling/admission evidence" });
  if ((value.admission_status === "settled") !== (progress.settlement !== null) || (progress.settlement !== null && value.status !== "settled") || (value.status === "settled" && progress.settlement === null && !["withdrawn", "historical_preserved"].includes(value.outcome))) ctx.addIssue({ code: "custom", message: "settled remediation requires genuine post-application completion; terminal controls remain distinct" });
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
export const FirstDepartureScopeSchema = immutable(RawFirstDepartureScopeSchema);
export const FirstDepartureRouteSchema = immutable(RawFirstDepartureRouteSchema);
export const FirstDepartureSelectionSchema = immutable(RawFirstDepartureSelectionSchema);
export const FirstDepartureWitnessSchema = immutable(RawFirstDepartureWitnessSchema);
export const EncounterTargetSchema = immutable(RawEncounterTargetSchema);
export const IssuedAttemptEvidenceSchema = immutable(RawIssuedAttemptEvidenceSchema);
export const RemediationFenceSchema = immutable(RawRemediationFenceSchema);
export const EncounterSettlementSchema = immutable(RawEncounterSettlementSchema);
export const EncounterProgressSchema = immutable(RawEncounterProgressSchema);
export const GameEffectKeySchema = immutable(RawGameEffectKeySchema);
export const GameEncounterSchema = immutable(RawGameEncounterSchema);
export const GameLapseSchema = immutable(RawGameLapseSchema);
export const EvidenceWithdrawalSchema = immutable(RawEvidenceWithdrawalSchema);
export const GameEffectControlSchema = immutable(RawGameEffectControlSchema);
export const LearningReceiptSchema = immutable(RawLearningReceiptSchema);
export const EncounterReceiptSchema = immutable(RawEncounterReceiptSchema);

export type GamePage = z.infer<typeof GamePageSchema>;
export type Finding = z.infer<typeof FindingSchema>;
export type RepertoireSnapshot = z.infer<typeof RepertoireSnapshotSchema>;
export type FirstDepartureSelection = z.infer<typeof FirstDepartureSelectionSchema>;
export type FirstDepartureWitness = z.infer<typeof FirstDepartureWitnessSchema>;
export type EncounterTarget = z.infer<typeof EncounterTargetSchema>;
export type IssuedAttemptEvidence = z.infer<typeof IssuedAttemptEvidenceSchema>;
export type RemediationFence = z.infer<typeof RemediationFenceSchema>;
export type EncounterProgress = z.infer<typeof EncounterProgressSchema>;
export type GameEffectKey = z.infer<typeof GameEffectKeySchema>;
export type GameEncounter = z.infer<typeof GameEncounterSchema>;
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

const count = z.number().int().min(0).max(Number.MAX_SAFE_INTEGER);
const probability = z.number().min(0).max(1);
const PositionMoveUniverseSchema = z.strictObject({ fen_key: id, legal_moves: z.array(uci) }).superRefine((value, ctx) => {
  try {
    if (value.fen_key.split(" ").length !== 4) throw new Error("four-field position required");
    const board = new Chess(`${value.fen_key} 0 1`);
    const legal = board.moves({ verbose: true }).map(move => move.from + move.to + (move.promotion ?? "")).sort();
    if (!legal.length || JSON.stringify([...value.legal_moves].sort()) !== JSON.stringify(legal) || board.fen().split(" ").slice(0, 4).join(" ") !== value.fen_key) throw new Error("incorrect legal inventory/position key");
  } catch { ctx.addIssue({ code: "custom", message: "invalid exact position or legal move inventory" }); }
});
const MoveProbabilityDistributionSchema = z.strictObject({ contract_version: z.literal(1).default(1), position: PositionMoveUniverseSchema, moves: z.array(z.strictObject({ move_uci: uci, probability: probability.nullable() })), unknown_mass: probability }).superRefine((value, ctx) => {
  const moves = value.moves.map(move => move.move_uci).sort();
  const total = value.moves.reduce((sum, move) => sum + (move.probability ?? 0), 0);
  if (JSON.stringify(moves) !== JSON.stringify([...value.position.legal_moves].sort()) || Math.abs(total + value.unknown_mass - 1) > 1e-9 || (value.moves.some(move => move.probability === null) && value.unknown_mass <= 0)) ctx.addIssue({ code: "custom", message: "preserve legal inventory and unknown probability mass" });
});
const CountEvidenceSchema = z.strictObject({ kind: z.literal("counts"), total_count: count, moves: z.array(z.strictObject({ move_uci: uci, count })) }).refine(value => new Set(value.moves.map(move => move.move_uci)).size === value.moves.length && value.moves.reduce((sum, move) => sum + move.count, 0) <= value.total_count, "invalid count evidence");
const RawPopulationSelectionSchema = z.strictObject({ rating_min: count, rating_max: count, time_controls: z.array(id).min(1) }).refine(value => value.rating_min <= value.rating_max && new Set(value.time_controls).size === value.time_controls.length, "explicit ordered population required");
const RawProbabilityEvidenceSchema = z.strictObject({ interface_version: version, evidence_id: id, position: PositionMoveUniverseSchema, provider: id, model: id.nullable(), basis: z.enum(["population", "personal"]), population: RawPopulationSelectionSchema.nullable(), personal_account: id.nullable(), observed_at: utc.nullable(), distribution: MoveProbabilityDistributionSchema.nullable(), counts: CountEvidenceSchema.nullable(), unavailable_reason: id.nullable() }).superRefine((value, ctx) => {
  if ((value.basis === "population") !== (value.population !== null) || (value.basis === "personal") !== (value.personal_account !== null)) ctx.addIssue({ code: "custom", message: "separate population/personal contexts" });
  if (value.distribution === null ? value.unavailable_reason === null : value.distribution.position.fen_key !== value.position.fen_key || value.unavailable_reason !== null || value.observed_at === null) ctx.addIssue({ code: "custom", message: "bind position/date or preserve unavailable reason" });
  if (value.counts?.moves.some(move => !value.position.legal_moves.includes(move.move_uci))) ctx.addIssue({ code: "custom", message: "illegal count move" });
});

const RawEngineContextSchema = z.strictObject({ starting_fen: id, history_uci: z.array(uci), target_fen: id, variant: id, learner_color: z.enum(["white", "black"]), engine_version: id, max_depth: z.number().int().min(1).max(128).nullable(), max_nodes: revision.nullable(), max_time_ms: revision.nullable(), threads: z.number().int().min(1).max(64), hash_mb: revision }).refine(value => value.max_depth !== null || value.max_nodes !== null || value.max_time_ms !== null, "engine work requires a bound");
const EngineScoreSchema = z.discriminatedUnion("kind", [z.strictObject({ kind: z.literal("centipawns"), value: z.number().int().min(-Number.MAX_SAFE_INTEGER).max(Number.MAX_SAFE_INTEGER) }), z.strictObject({ kind: z.literal("mate"), value: z.number().int().min(-1000).max(1000) })]);
const RawCandidateEvaluationSchema = z.strictObject({ move_uci: uci, score: EngineScoreSchema.nullable(), score_perspective: z.literal("learner").default("learner"), depth: z.number().int().min(1).max(128).nullable(), nodes: count.nullable(), unavailable_reason: id.nullable() }).refine(value => (value.score === null) === (value.unavailable_reason !== null), "missing evaluation requires reason");
const RawEngineEvidenceSchema = z.strictObject({ interface_version: version, evidence_id: id, context: RawEngineContextSchema, requested_candidates: z.array(uci).min(1), best: RawCandidateEvaluationSchema.nullable(), candidates: z.array(RawCandidateEvaluationSchema), unavailable_reason: id.nullable() }).superRefine((value, ctx) => {
  const evaluated = value.candidates.map(candidate => candidate.move_uci);
  if (new Set(value.requested_candidates).size !== value.requested_candidates.length || new Set(evaluated).size !== evaluated.length || JSON.stringify([...evaluated].sort()) !== JSON.stringify([...value.requested_candidates].sort())) ctx.addIssue({ code: "custom", message: "every explicit candidate requires evidence" });
  if ((value.best === null) !== (value.unavailable_reason !== null)) ctx.addIssue({ code: "custom", message: "missing best requires reason" });
});

const RepertoireEditSchema = z.strictObject({ route_id: id, position_key: id, previous_response_uci: uci.nullable(), proposed_response_uci: uci, displayed_pgn: z.string().min(1) });
const DisplayedCardChangeSchema = z.strictObject({ action: z.enum(["add", "update", "reuse"]), presentation_id: id.nullable(), expected_revision: revision.nullable(), starting_fen: id, moves: z.array(uci).min(1), learner_color: z.enum(["white", "black"]) }).refine(value => value.action === "add" || (value.presentation_id !== null && value.expected_revision !== null), "existing card requires identity/revision");
const RawProposalSchema = z.strictObject({ interface_version: version, proposal_id: id, proposal_revision: revision, repertoire_id: id, source_revision: revision, source_digest: digest, display_digest: digest, title: id, explanation: z.string().min(1), edits: z.array(RepertoireEditSchema), card_changes: z.array(DisplayedCardChangeSchema), engine_evidence: z.array(RawEngineEvidenceSchema), supporting_position_keys: z.array(id), max_loss_cp: count.default(30), threshold_adjustment_reason: id.nullable().default(null) }).refine(value => (value.max_loss_cp === 30 || value.threshold_adjustment_reason !== null) && new Set(value.supporting_position_keys).size === value.supporting_position_keys.length, "display threshold changes and count distinct support positions");
const RawProposalDecisionSchema = z.strictObject({ proposal_id: id, expected_proposal_revision: revision, expected_source_revision: revision, display_digest: digest, action: z.enum(["accept", "edit", "dismiss"]), edited_proposal: RawProposalSchema.nullable() }).refine(value => (value.action === "edit") === (value.edited_proposal !== null), "edit requires a fresh preview");
const RawRepertoireChangeReceiptSchema = z.strictObject({ owner_receipt_id: id, proposal_id: id, status: z.enum(["preview", "accepted", "dismissed", "conflict", "unavailable"]), source_revision: revision, proposal: RawProposalSchema.nullable(), reason: id.nullable() });

const DecisionComponentSchema = z.strictObject({ decision_id: id, position_key: id, response_uci: uci, resulting_position_key: id });
const OpponentCueSchema = z.strictObject({ position_key: id, move_uci: uci, resulting_position_key: id });
const RawPresentationSchema = z.strictObject({ interface_version: version, presentation_id: id, revision, repertoire_id: id, repertoire_revision: revision, variant: id, learner_color: z.enum(["white", "black"]), route_id: id, components: z.array(DecisionComponentSchema).min(1), opponent_cues: z.array(OpponentCueSchema), clean_streak: count, introduced: z.boolean(), lineage: z.array(id), recorded_parent_id: id.nullable() });
const RawJoinPlanSchema = z.strictObject({ interface_version: version, plan_id: id, expected_study_revision: revision, left: RawPresentationSchema, right: RawPresentationSchema, connecting_cues: z.array(OpponentCueSchema), result: RawPresentationSchema, resulting_clean_streak: z.literal(0).default(0), introduction_slots: z.literal(0).default(0), unresolved_obligation_ids: z.array(id) }).superRefine((value, ctx) => {
  const scope = (item: z.infer<typeof PresentationSchema>) => JSON.stringify([item.repertoire_id, item.repertoire_revision, item.variant, item.learner_color, item.route_id]);
  if (scope(value.left) !== scope(value.right) || scope(value.left) !== scope(value.result)) ctx.addIssue({ code: "custom", message: "join must retain route-local scope" });
  if (new Set([value.left.presentation_id, value.right.presentation_id, value.result.presentation_id]).size !== 3 || value.result.clean_streak !== 0 || JSON.stringify(value.result.components) !== JSON.stringify([...value.left.components, ...value.right.components])) ctx.addIssue({ code: "custom", message: "join requires a fresh 0/3 combination of existing components" });
  if (!(value.left.introduced && value.right.introduced && value.result.introduced && value.result.lineage.includes(value.left.presentation_id) && value.result.lineage.includes(value.right.presentation_id))) ctx.addIssue({ code: "custom", message: "retain introduced components and lineage" });
  if (JSON.stringify(value.result.opponent_cues) !== JSON.stringify([...value.left.opponent_cues, ...value.connecting_cues, ...value.right.opponent_cues])) ctx.addIssue({ code: "custom", message: "preserve exact opponent cues" });
  let position = value.left.components[value.left.components.length - 1].resulting_position_key;
  for (const cue of value.connecting_cues) {
    if (cue.position_key !== position) ctx.addIssue({ code: "custom", message: "connecting cue must retain exact position" });
    position = cue.resulting_position_key;
  }
  if (position !== value.right.components[0].position_key) ctx.addIssue({ code: "custom", message: "cues must reach the existing next decision" });
});
const RawPresentationChangeReceiptSchema = z.strictObject({ owner_receipt_id: id, plan_id: id, status: z.enum(["applied", "stale_revision", "active_attempt", "ineligible"]), resulting_presentation_id: id.nullable(), reason: id.nullable() });
const RankingCandidateSchema = z.strictObject({ decision_id: id, repertoire_id: id, reach_probability: probability.nullable(), evidence_support: count.nullable(), uncertainty: probability.nullable(), personal_encounters: count.nullable() });
const RawRankingInputSchema = z.strictObject({ repertoire_revisions: z.array(z.tuple([id, revision])), candidates: z.array(RankingCandidateSchema), evidence_ids: z.array(id) });
const RawRankingOutputSchema = z.strictObject({ based_on: RawRankingInputSchema, ordered: z.array(z.strictObject({ decision_id: id, rank: revision, reason: id })), unavailable_reason: id.nullable() });

export const PopulationSelectionSchema = immutable(RawPopulationSelectionSchema);
export const ProbabilityEvidenceSchema = immutable(RawProbabilityEvidenceSchema);
export const EngineContextSchema = immutable(RawEngineContextSchema);
export const CandidateEvaluationSchema = immutable(RawCandidateEvaluationSchema);
export const EngineEvidenceSchema = immutable(RawEngineEvidenceSchema);
export const ProposalSchema = immutable(RawProposalSchema);
export const ProposalDecisionSchema = immutable(RawProposalDecisionSchema);
export const RepertoireChangeReceiptSchema = immutable(RawRepertoireChangeReceiptSchema);
export const PresentationSchema = immutable(RawPresentationSchema);
export const JoinPlanSchema = immutable(RawJoinPlanSchema);
export const PresentationChangeReceiptSchema = immutable(RawPresentationChangeReceiptSchema);
export const RankingInputSchema = immutable(RawRankingInputSchema);
export const RankingOutputSchema = immutable(RawRankingOutputSchema);

export type ProbabilityEvidence = z.infer<typeof ProbabilityEvidenceSchema>;
export type EngineEvidence = z.infer<typeof EngineEvidenceSchema>;
export type Proposal = z.infer<typeof ProposalSchema>;
export type ProposalDecision = z.infer<typeof ProposalDecisionSchema>;
export type RepertoireChangeReceipt = z.infer<typeof RepertoireChangeReceiptSchema>;
export type Presentation = z.infer<typeof PresentationSchema>;
export type JoinPlan = z.infer<typeof JoinPlanSchema>;
export type RankingInput = z.infer<typeof RankingInputSchema>;
export type RankingOutput = z.infer<typeof RankingOutputSchema>;

/** Same canonical SHA-256 binding as Proposal.content_digest(); Repertoire owns approval. */
export async function proposalDisplayDigest(proposal: Proposal, subtle: SubtleCrypto = globalThis.crypto.subtle): Promise<string> {
  const { display_digest: _binding, ...payload } = proposal;
  function ordered(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(ordered);
    if (value !== null && typeof value === "object") return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([key, item]) => [key, ordered(item)]));
    return value;
  }
  const bytes = new TextEncoder().encode(JSON.stringify(ordered(payload)));
  const result = await subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(result), byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function matchesProposalPreview(decision: ProposalDecision, proposal: Proposal, subtle?: SubtleCrypto): Promise<boolean> {
  return decision.proposal_id === proposal.proposal_id && decision.expected_proposal_revision === proposal.proposal_revision && decision.expected_source_revision === proposal.source_revision && decision.display_digest === proposal.display_digest && proposal.display_digest === await proposalDisplayDigest(proposal, subtle);
}

export interface RepertoireChangePort {
  preview(proposal: Proposal): Promise<RepertoireChangeReceipt>;
  decide(decision: ProposalDecision): Promise<RepertoireChangeReceipt>;
}
export interface RepertoireReviewApi extends RepertoireChangePort {
  proposals(repertoireId: string): Promise<readonly Proposal[]>;
  evidence(positionKey: string): Promise<readonly ProbabilityEvidence[]>;
}

export const PRODUCT_SCHEMAS = { GamePage: GamePageSchema, Finding: FindingSchema, RepertoireSnapshot: RepertoireSnapshotSchema, FirstDepartureSelection: FirstDepartureSelectionSchema, FirstDepartureWitness: FirstDepartureWitnessSchema, GameEncounter: GameEncounterSchema, GameLapse: GameLapseSchema, GameEffectControl: GameEffectControlSchema, LearningReceipt: LearningReceiptSchema, EncounterReceipt: EncounterReceiptSchema, GameSummary: GameSummarySchema, ProviderSyncStatus: ProviderSyncStatusSchema, RecentGames: RecentGamesSchema, GameDetail: GameDetailSchema, PopulationSelection: PopulationSelectionSchema, ProbabilityEvidence: ProbabilityEvidenceSchema, EngineContext: EngineContextSchema, CandidateEvaluation: CandidateEvaluationSchema, EngineEvidence: EngineEvidenceSchema, Proposal: ProposalSchema, ProposalDecision: ProposalDecisionSchema, RepertoireChangeReceipt: RepertoireChangeReceiptSchema, Presentation: PresentationSchema, JoinPlan: JoinPlanSchema, PresentationChangeReceipt: PresentationChangeReceiptSchema, RankingInput: RankingInputSchema, RankingOutput: RankingOutputSchema } as const;
