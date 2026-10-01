import { z } from "zod";

const count = z.number().int().nonnegative();
export const segmentationRecommendationSchema = z.object({
  id: z.string(), repertoire_id: z.string(), kind: z.enum(["shared_trunk", "transposition"]),
  decisions_before: count, decisions_after: count, decisions_avoided: count,
  additional_starts: count, segment_count: count,
});
export const segmentationListSchema = z.object({
  version: z.literal(1), preview_only: z.literal(true),
  state: z.enum(["unavailable", "stale", "building", "ready", "failed"]),
  content_version: count, graph_generation: count.nullable(), error: z.string().nullable(),
  invalidated_pins: count, recommendations: z.array(segmentationRecommendationSchema),
});
export const segmentationDetailSchema = z.object({
  version: z.literal(1), preview_only: z.literal(true), content_version: count,
  graph_generation: count, source_fingerprint: z.string(),
  recommendation: segmentationRecommendationSchema, rationale: z.string(), estimate_basis: z.string(),
  segments: z.array(z.object({
    id: z.string(), role: z.enum(["shared_trunk", "branch", "bridge", "shared_continuation"]),
    starting_fen: z.string(), ending_fen: z.string(), moves: z.array(z.string()),
    tested_decisions: count, trained_color: z.enum(["white", "black"]),
  })),
  next_segment: z.string().nullable(), routes: z.array(z.object({ id: z.string(), name: z.string() })),
  next_route: z.string().nullable(),
});
export type SegmentationDetail = z.infer<typeof segmentationDetailSchema>;
