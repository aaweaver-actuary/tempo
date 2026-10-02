import * as z from "zod";
import { fenStringSchema, uciMoveSchema } from "./schemas/primitives";

export const canonicalPrefixSchema = z.strictObject({
  moves_uci: z.array(uciMoveSchema), san: z.string(),
  ending_fen: fenStringSchema, revision: z.number().int().nonnegative(),
});
export type CanonicalPrefix = z.infer<typeof canonicalPrefixSchema>;

export const canonicalPrefixPreviewAdmissionSchema = canonicalPrefixSchema.extend({
  preview_id: z.string().min(1), state: z.literal("checking"),
});
export const canonicalPrefixPreviewSchema = canonicalPrefixSchema.extend({
  preview_id: z.string().min(1),
  state: z.enum(["checking", "ready", "conflicts", "stale", "failed"]),
  error: z.string().nullable(), suggestion: canonicalPrefixSchema,
  conflicts: z.array(z.strictObject({
    item_id: z.string(), name: z.string(), reason: z.string(),
    disagreement_ply: z.number().int().nonnegative().nullable(),
  })),
  conflict_count: z.number().int().nonnegative(), next_cursor: z.string().nullable(),
});
export type CanonicalPrefixPreview = z.infer<typeof canonicalPrefixPreviewSchema>;
