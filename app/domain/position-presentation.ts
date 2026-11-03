import * as z from "zod";
import { Chess } from "chess.js";
import { canonicalFenKey as fourFieldFenKey } from "@tempo/chess-line";
import {
  fenKeySchema,
  fenStringSchema,
  uciMoveSchema,
} from "./schemas/primitives";

/** Position Study identity includes en passant only when a legal capture exists. */
export function legalPositionKey(fen: string): string {
  return fourFieldFenKey(new Chess(fen).fen());
}

const digestSchema = z.string().regex(/^[0-9a-f]{64}$/);
const nonEmpty = z.string().min(1);
const safePositiveInteger = z.number().int().positive().refine(Number.isSafeInteger);
const safeNonNegativeInteger = z.number().int().nonnegative().refine(Number.isSafeInteger);
const canonicalFen = z.string().refine(
  (value) => fenStringSchema.safeParse(value).success && fenStringSchema.parse(value) === value,
);
const canonicalFenKey = z.string().refine(
  (value) => fenKeySchema.safeParse(value).success && fenKeySchema.parse(value) === value,
);
const canonicalUci = z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/).pipe(uciMoveSchema);

export const positionDecisionSlotSchema = z.strictObject({
  move_offset: safeNonNegativeInteger,
  position_key: canonicalFenKey,
  expected_uci: canonicalUci,
});

/** Strict browser-facing v1 envelope issued by the Study server. */
export const positionPresentationSchema = z
  .strictObject({
    version: z.literal(1),
    plan_id: digestSchema,
    original_card_id: nonEmpty,
    original_card_revision: safePositiveInteger,
    queue_entry_id: safePositiveInteger,
    attempt_id: nonEmpty,
    owner_epoch: nonEmpty,
    route_id: nonEmpty,
    target_ply: safeNonNegativeInteger,
    starting_fen: canonicalFen,
    moves_uci: z.array(canonicalUci).max(40),
    decision_slots: z.array(positionDecisionSlotSchema).min(1).max(20),
    source_digest: nonEmpty,
    local_day: z.iso.date(),
    skipped_prefix_proof_ids: z.array(nonEmpty),
  })
  .superRefine((plan, context) => {
    const seen = new Set<number>();
    let previous = -1;
    plan.decision_slots.forEach((slot, index) => {
      if (slot.move_offset >= plan.moves_uci.length) {
        context.addIssue({
          code: "custom",
          path: ["decision_slots", index, "move_offset"],
          message: "Decision offset must reference a move in the issued line",
        });
      }
      if (seen.has(slot.move_offset) || slot.move_offset <= previous) {
        context.addIssue({
          code: "custom",
          path: ["decision_slots", index, "move_offset"],
          message: "Decision offsets must be unique and ascending",
        });
      }
      if (plan.moves_uci[slot.move_offset] !== slot.expected_uci) {
        context.addIssue({
          code: "custom",
          path: ["decision_slots", index, "expected_uci"],
          message: "Expected move must match the issued move at this offset",
        });
      }
      seen.add(slot.move_offset);
      previous = slot.move_offset;
    });
  });

type ParsedPositionPresentation = z.infer<typeof positionPresentationSchema>;
export type PositionPresentation = Readonly<
  Omit<
    ParsedPositionPresentation,
    "moves_uci" | "decision_slots" | "skipped_prefix_proof_ids"
  > & {
    moves_uci: readonly ParsedPositionPresentation["moves_uci"][number][];
    decision_slots: readonly Readonly<
      ParsedPositionPresentation["decision_slots"][number]
    >[];
    skipped_prefix_proof_ids: readonly string[];
  }
>;

function freezeDecodedPlan(
  parsed: z.infer<typeof positionPresentationSchema>,
): PositionPresentation {
  const decisionSlots = Object.freeze(
    parsed.decision_slots.map((slot) => Object.freeze({ ...slot })),
  );
  return Object.freeze({
    ...parsed,
    moves_uci: Object.freeze([...parsed.moves_uci]),
    decision_slots: decisionSlots,
    skipped_prefix_proof_ids: Object.freeze([...parsed.skipped_prefix_proof_ids]),
  });
}

/**
 * Decode an issued plan for display. The digest is an identifier, not a
 * signature: only the Study server can establish that this plan was issued.
 */
export function decodePositionPresentation(raw: unknown): PositionPresentation {
  return freezeDecodedPlan(positionPresentationSchema.parse(raw));
}
