import { z } from "zod";
import { prefixSourceSchema, prefixComparisonSchema, type PrefixSource } from "./prefix-comparison";

const identity = z.string().min(1);
const count = z.number().int().nonnegative();
const depth = z.number().int().min(1).max(20);
const day = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
export const prefixTransitionRequestSchema = z.strictObject({
  snapshot_id: identity, selected_line_ids: z.array(identity).max(2000),
  candidate_depths: z.record(identity, depth),
});
const cardDispositionSchema = z.strictObject({
  card_id: identity,
  classification: z.enum(["unchanged", "reuse_existing", "new_replacement", "retained_shared", "retired", "conflicting"]),
  expected_revision: count.nullable(), lifecycle: z.enum(["preserve", "create", "archive", "blocked"]),
  owner_before: z.string().nullable(), owner_after: z.string().nullable(), kind_after: z.string().nullable(),
  state_handling: z.enum(["preserve", "fresh", "blocked"]), schedule_json: z.string().nullable(),
  schedule_seed_json: z.string().nullable(), review_ids: z.array(count),
  history_handling: z.literal("retain_on_original_identity"), new_review_count: z.literal(0),
  new_decision_observation_count: z.literal(0),
});
export const prefixTransitionPlanSchema = z.strictObject({
  version: z.literal(1), dry_run: z.literal(true), repertoire_id: identity, snapshot_id: identity,
  transition_snapshot_id: identity, graph_generation: z.number().int().positive(),
  graph_policy_version: count, position_version: count, structural_version: count, study_day: day,
  plan_id: identity, status: z.enum(["no_op", "ready", "blocked"]), selected_line_ids: z.array(identity),
  depth_changes: z.array(z.strictObject({ line_id: identity, before: depth, after: depth })),
  current_steps: prefixComparisonSchema.shape.whole_repertoire.shape.current.shape.steps,
  proposed_steps: prefixComparisonSchema.shape.whole_repertoire.shape.proposed.shape.steps,
  cards: z.array(cardDispositionSchema),
  memberships: z.array(z.strictObject({ repertoire_id: identity, card_id: identity,
    action: z.enum(["preserve", "add_generated", "obsolete", "blocked"]), canonical_route_source: z.number().int() })),
  attempts: z.array(z.strictObject({ kind: z.enum(["queue", "origin", "opening_attempt", "study_attempt", "pending_command", "receipt"]),
    object_id: identity, card_id: identity,
    action: z.enum(["preserve", "supersede_projection", "retain_for_recovery", "retire_with_conflict", "replay_receipt", "blocked"]) })),
  submissions: z.array(z.strictObject({ card_id: identity, expected_revision: count.nullable(),
    completed_receipt: z.literal("replay_original_result"),
    unresolved_submission: z.enum(["existing_identity_recovery", "archived_identity_conflict", "blocked"]),
    replacement_credit: z.literal(false) })),
  blockers: z.array(z.strictObject({ code: identity, object_id: z.string(), reason: identity })),
  offline_visibility: z.literal("client_local_attempts_not_enumerable"),
});
export const prefixTransitionApplySchema = prefixTransitionRequestSchema.extend({
  plan_id: identity, transition_snapshot_id: identity, graph_generation: z.number().int().positive(), study_day: day,
});
export const prefixTransitionResultSchema = z.strictObject({
  status: z.literal("complete"), operation_id: identity, plan_id: identity, repertoire_id: identity,
  graph_generation: z.number().int().positive(), no_op: z.boolean(), queue_date: day.optional(),
});
const operationErrorSchema = z.object({ message: z.string().optional(), code: z.string().optional(),
  status_code: z.number().optional(), detail: z.unknown().optional() });
export const prefixTransitionReceiptSchema = z.object({
  operation_id: identity, state: z.enum(["unknown", "queued", "executing", "retrying", "pending", "blocked", "complete", "failed"]),
  response: z.unknown().optional(), error: operationErrorSchema.nullish(), last_error: operationErrorSchema.nullish(),
  message: z.string().optional(), transition: z.object({
    state: z.enum(["staging", "staged", "publishing", "recovery_required", "complete", "rejected"]),
    graph_generation: z.number().int().positive(), queue_date: day.nullable(), recovery: z.string().nullable(),
  }).optional(),
});
export const prefixTransitionSummarySchema = z.strictObject({
  repertoireName: z.string(), routes: prefixSourceSchema.shape.lines,
  counts: z.strictObject({ unchanged: count, reused: count, added: count, retired: count, retainedShared: count, conflicting: count }),
  preservedReviews: count, retiredAttempts: count, retiredCommands: count, supersededQueue: count, replayedReceipts: count,
  affectedRepertoireIds: z.array(identity),
});
export type PrefixTransitionPlan = z.infer<typeof prefixTransitionPlanSchema>;
export type PrefixTransitionRequest = z.infer<typeof prefixTransitionApplySchema>;
export type PrefixTransitionSummary = z.infer<typeof prefixTransitionSummarySchema>;
export type PrefixTransitionReceipt = z.infer<typeof prefixTransitionReceiptSchema>;
export type PrefixTransitionResult = z.infer<typeof prefixTransitionResultSchema>;

export function transitionRequest(plan: PrefixTransitionPlan, candidateDepths: Record<string, number>): PrefixTransitionRequest {
  return prefixTransitionApplySchema.parse({ snapshot_id: plan.snapshot_id, selected_line_ids: plan.selected_line_ids,
    candidate_depths: candidateDepths, plan_id: plan.plan_id, transition_snapshot_id: plan.transition_snapshot_id,
    graph_generation: plan.graph_generation, study_day: plan.study_day });
}

export function transitionSummary(plan: PrefixTransitionPlan, source: PrefixSource, repertoireName: string): PrefixTransitionSummary {
  const classificationCount = (classification: PrefixTransitionPlan["cards"][number]["classification"]) =>
    plan.cards.filter(card => card.classification === classification).length;
  return prefixTransitionSummarySchema.parse({ repertoireName,
    routes: plan.selected_line_ids.map(id => source.lines.find(line => line.id === id)),
    counts: { unchanged: classificationCount("unchanged"), reused: classificationCount("reuse_existing"),
      added: classificationCount("new_replacement"), retired: classificationCount("retired"),
      retainedShared: classificationCount("retained_shared"), conflicting: classificationCount("conflicting") },
    preservedReviews: new Set(plan.cards.flatMap(card => card.review_ids)).size,
    retiredAttempts: plan.attempts.filter(attempt => ["opening_attempt", "study_attempt"].includes(attempt.kind) && attempt.action === "retire_with_conflict").length,
    retiredCommands: plan.attempts.filter(attempt => attempt.kind === "pending_command" && attempt.action === "retire_with_conflict").length,
    supersededQueue: plan.attempts.filter(attempt => attempt.action === "supersede_projection").length,
    replayedReceipts: plan.attempts.filter(attempt => attempt.action === "replay_receipt").length,
    affectedRepertoireIds: [...new Set([plan.repertoire_id, ...plan.memberships.map(link => link.repertoire_id),
      ...plan.cards.flatMap(card => [card.owner_before, card.owner_after]).filter((id): id is string => id !== null)])].sort(),
  });
}
