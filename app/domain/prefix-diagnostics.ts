import { z } from "zod";
import { assistanceKindSchema, openingDecisionManifestSchema } from "./opening-evidence";
const count = z.number().int().nonnegative();
export const diagnosticPrefixSchema = z.strictObject({
  card_id: z.string(), presentation_san: z.string().nullable(), manifest: openingDecisionManifestSchema.nullable(), unavailable_reason: z.string().nullable(),
});
export const prefixDiagnosticsListSchema = z.strictObject({
  version: z.literal(1), repertoire_id: z.string(), graph_generation: count,
  prefixes: z.array(diagnosticPrefixSchema).max(8), next_card_id: z.string().nullable(),
});
const observationSchema = z.strictObject({
  attempt_id: z.string(), state: z.enum(["active", "partial", "complete"]),
  decision_index: count, decision_id: z.string(), expected_uci: z.string(), first_response_uci: z.string().nullable(),
  first_response_correct: z.boolean().nullable(), assistance_before_response: z.array(assistanceKindSchema),
  assistance: z.array(assistanceKindSchema), manual_failure: z.boolean(), revealed: z.boolean(), corrected: z.boolean(),
  observed_at: z.string(), response_at: z.string().nullable(), failure_at: z.string().nullable(),
  disposition: z.enum(["expected", "alternate", "wrong", "illegal", "unverified"]).nullable(), clean: z.boolean(), study_day: z.string(),
});
export const prefixDiagnosticsDetailSchema = z.strictObject({
  version: z.literal(1), read_only: z.literal(true), graph_generation: count, manifest: openingDecisionManifestSchema,
  window: z.strictObject({ attempt_limit: z.literal(100), attempt_count: count.max(100), older_attempts_excluded: z.boolean(),
    newest_started_at: z.string().nullable(), oldest_started_at: z.string().nullable() }),
  decisions: z.array(openingDecisionManifestSchema.shape.decisions.element.extend({
    coverage: z.enum(["unknown", "weak", "strong"]), reached_observations: count, first_responses: count,
    first_response_failures: count, unassisted_first_responses: count, unassisted_first_response_failures: count,
    clean_successes: count, assistance_before_response: count,
    assistance_categories: z.partialRecord(assistanceKindSchema, count),
    manual_failures: count, corrections: count, reveals: count, distinct_clean_days: count,
    distinct_unassisted_response_days: count, recent_outcomes: z.array(observationSchema).max(20),
  })).min(1).max(20),
});
export type PrefixDiagnosticsList = z.infer<typeof prefixDiagnosticsListSchema>;
export type PrefixDiagnosticsDetail = z.infer<typeof prefixDiagnosticsDetailSchema>;
export type DiagnosticPrefix = z.infer<typeof diagnosticPrefixSchema>;
