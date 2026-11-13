/** Retained grader-v1 exercise transport. These decoders do not perform I/O. */
import * as z from "zod";
import { validateFen } from "chess.js";
import { studySpecificationSchema } from "./study-exercises";

const id = z.string().min(1);
const revision = z.number().int().positive();
const square = z.string().regex(/^[a-h][1-8]$/);
const fen = z.string().refine(value => validateFen(value).ok, "Invalid exercise FEN");
const common = { id, revision, prompt: z.string().min(1).max(2000), hint: z.string().max(2000), fen };
const presentation = z.discriminatedUnion("type", [
  z.strictObject({ ...common, type: z.literal("move_line"), mode: z.enum(["single", "stepwise_line"]), grading_policy: z.enum(["reference", "open_judgment"]) }),
  z.strictObject({ ...common, type: z.literal("square_set"), criterion: z.string().min(1).max(2000), candidate_region: z.array(square).max(64).nullable() }),
  z.strictObject({ ...common, type: z.literal("knight_path"), start_square: square, target_squares: z.array(square).min(1).max(8), minimum_hops: z.number().int().min(0).max(3), maximum_hops: z.number().int().min(0).max(3), hop_rule: z.enum(["at_most", "exact"]), occupancy_rule: z.literal("static_non_capturing") }),
  z.strictObject({ ...common, type: z.literal("choice"), options: z.array(z.strictObject({ id: z.string().min(1).max(80), text: z.string().min(1).max(1000) })).min(2).max(12) }),
  z.strictObject({ ...common, type: z.literal("explanation") }),
]);
const assessment = z.object({
  outcome: z.enum(["correct", "incorrect", "unrecognized", "needs_self_assessment"]), feedback: z.string(),
  omitted: z.array(square).max(64).optional(), extra: z.array(square).max(64).optional(), outside_region: z.array(square).max(64).optional(),
}).strict();
const pending = z.strictObject({ attempt_id: id, assessment, pending_self_assessment: z.literal(true) })
  .refine(value => ["unrecognized", "needs_self_assessment"].includes(value.assessment.outcome));
const completed = z.strictObject({ attempt_id: id, assessment, rating: z.enum(["correct", "again"]),
  requested_rating: z.enum(["correct", "again"]), persisted: z.literal(true),
  review: z.object({ review_id: z.number().int().positive(), requeue_entry_id: z.number().int().positive().nullable() }).passthrough().nullable(),
});
const specification = studySpecificationSchema.superRefine((value, ctx) => {
  const invalid = (message: string) => ctx.addIssue({ code: "custom", message });
  if (value.prompt.length > 2000 || value.hint.length > 2000 || value.explanation.length > 10000 || (value.further_analysis?.length ?? 0) > 10000) invalid("Exercise text exceeds owner bounds");
  if (value.type === "move_line" && (value.accepted_lines.length < 1 || value.accepted_lines.length > 20 || value.accepted_lines.some(line => line.length < 1 || line.length > 40 || line.some(move => !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move))))) invalid("Invalid accepted lines");
  if (value.type === "square_set" && (value.required.length > 64 || value.optional.length > 64 || (value.candidate_region?.length ?? 0) > 64 || value.criterion.length > 2000)) invalid("Invalid square projection");
  if (value.type === "knight_path" && (value.target_squares.length < 1 || value.target_squares.length > 8 || value.minimum_hops > value.maximum_hops)) invalid("Invalid knight projection");
  if (value.type === "choice" && (value.options.length < 2 || value.options.length > 12 || value.options.some(option => !option.id.length || option.id.length > 80 || !option.text.length || option.text.length > 1000) || !value.correct_option_ids.length)) invalid("Invalid choices");
  if (value.type === "explanation" && value.rubric.length > 10000) invalid("Invalid rubric");
});
const feedback = z.strictObject({ specification });

export type FrozenPresentation = z.infer<typeof presentation>;
export type FrozenAttempt = z.infer<typeof pending> | z.infer<typeof completed>;

export function decodeFrozenPresentation(value: unknown, expected: { exerciseId: string; revision?: number }): FrozenPresentation {
  const result = presentation.parse(value);
  if (result.id !== expected.exerciseId || (expected.revision !== undefined && result.revision !== expected.revision)) throw new Error("Exercise identity or revision changed");
  if (result.type === "knight_path" && result.minimum_hops > result.maximum_hops) throw new Error("Invalid hop bounds");
  return result;
}

/** A pending self-assessment is a completed parent command, not a completed review. */
export function decodeFrozenAttempt(value: unknown, expected: { attemptId: string; context: "review" | "practice" }): FrozenAttempt {
  const result = z.union([pending, completed]).parse(value);
  if (result.attempt_id !== expected.attemptId) throw new Error("Attempt identity changed");
  if ("persisted" in result && ((expected.context === "review") !== (result.review !== null))) throw new Error("Review receipt does not match context");
  return result;
}

/** Feedback identity and historical revision are verified by the HTTP owner before projection. */
export function decodeFrozenFeedback(value: unknown) {
  return feedback.parse(value);
}

/** Conservative classification; unknown authority and retired producers remain held. */
export function classifyFrozenWork(facts: { kind?: string; admissionSource?: string | null; linkedPublished?: boolean; originalRecovery?: boolean; ledgerRetired?: boolean; authorityKnown?: boolean }): "supported" | "recovery" | "held" {
  if (!facts.authorityKnown || facts.ledgerRetired || ["defense", "endgame"].includes(facts.kind ?? "") || /^(defense|endgame):/.test(facts.admissionSource ?? "")) return "held";
  if (facts.originalRecovery && facts.kind === "study_exercise") return "recovery";
  if (facts.kind === "study_exercise" && facts.linkedPublished) return "supported";
  return "held";
}
