/** Pure receipt compatibility; a client still reacquires server issuance authority. */
import { z } from "zod";

export const nextCardPreparationResultSchema = z.object({
  progressed: z.boolean(),
  pending: z.boolean(),
  reason: z.string().nullable(),
  blocks_issuance: z.boolean().optional(),
});
export type NextCardPreparationResult = z.infer<typeof nextCardPreparationResultSchema>;

export function preparationBlocksIssuance(value: NextCardPreparationResult): boolean {
  return value.blocks_issuance ?? value.pending;
}
