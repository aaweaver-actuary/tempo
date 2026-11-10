import * as z from "zod";
import { fenStringSchema, uciMoveSchema } from "./schemas/primitives";

export const canonicalPrefixSchema = z.strictObject({
  moves_uci: z.array(uciMoveSchema), san: z.string(),
  ending_fen: fenStringSchema, revision: z.number().int().nonnegative(),
});
export type CanonicalPrefix = z.infer<typeof canonicalPrefixSchema>;
