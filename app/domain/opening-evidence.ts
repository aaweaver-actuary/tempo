import * as z from "zod";

const identity = z.string().length(64);
const uci = z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/);
export const assistanceKindSchema = z.enum(["teaching", "hint", "revealed", "guided", "other"]);
export type AssistanceKind = z.infer<typeof assistanceKindSchema>;
export const openingDecisionManifestSchema = z.strictObject({
  manifest_version: z.literal(1), policy_version: z.literal(1), manifest_id: identity,
  presentation_snapshot_id: z.number().int().positive(), repertoire_id: z.string().min(1),
  card_id: z.string().min(1), card_revision: z.number().int().positive(), trained_color: z.enum(["white", "black"]),
  decisions: z.array(z.strictObject({ decision_id: identity, decision_index: z.number().int().min(0).max(19),
    move_offset: z.number().int().min(0).max(39), fen: z.string(), expected_uci: uci })).min(1).max(20),
});
export type OpeningDecisionManifest = z.infer<typeof openingDecisionManifestSchema>;
export const openingDecisionEventSchema = z.strictObject({
  sequence: z.number().int().min(1).max(256), decision_index: z.number().int().min(0).max(19),
  decision_id: identity, expected_uci: uci,
  kind: z.enum(["assistance", "first_response", "manual_failure", "reveal", "correction"]),
  observed_at: z.iso.datetime({ offset: true }), response_uci: uci.nullable().default(null),
  assistance: assistanceKindSchema.nullable().default(null),
  disposition: z.enum(["expected", "alternate", "wrong", "illegal", "unverified"]).nullable().default(null),
});
export type OpeningDecisionEvent = z.infer<typeof openingDecisionEventSchema>;
export const openingEvidenceCheckpointSchema = z.strictObject({
  attempt_id: z.string().min(1).max(100), manifest: openingDecisionManifestSchema,
  origin_queue_entry_id: z.number().int().positive(), queue_entry_id: z.number().int().positive().nullable().default(null),
  parent_attempt_id: z.string().min(1).max(100).nullable().default(null),
  started_at: z.iso.datetime({ offset: true }), study_timezone: z.string().min(1).max(100),
  source: z.enum(["live", "offline", "reinforcement"]).default("live"),
  events: z.array(openingDecisionEventSchema).max(256).default([]),
  terminal: z.strictObject({ state: z.enum(["partial", "complete"]), final_sequence: z.number().int().min(0).max(256),
    ended_at: z.iso.datetime({ offset: true }) }).nullable().default(null),
});
export type OpeningEvidenceCheckpoint = z.infer<typeof openingEvidenceCheckpointSchema>;
