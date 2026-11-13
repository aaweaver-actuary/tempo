/** Retained grader-v1 exercise transport. These decoders do not perform I/O. */
import * as z from "zod";
import { validateFen } from "chess.js";
import { studySpecificationSchema } from "./study-exercises";

const id = z.string().min(1);
// The command owner bounds Python characters, and permits an existing empty ID.
const length = (value: string) => Array.from(value).length;
const boundedText = (maximum: number, minimum = 0) => z.string().refine(value => length(value) >= minimum && length(value) <= maximum, "Text exceeds owner bounds");
const attemptId = boundedText(100);
const revision = z.number().int().positive();
const square = z.string().regex(/^[a-h][1-8]$/);
const fen = z.string().refine(value => validateFen(value).ok, "Invalid exercise FEN");
const common = { id, revision, prompt: boundedText(2000, 1), hint: boundedText(2000), fen };
const hasDuplicates = (values: string[]) => new Set(values).size !== values.length;
const presentation = z.discriminatedUnion("type", [
  z.strictObject({ ...common, type: z.literal("move_line"), mode: z.enum(["single", "stepwise_line"]), grading_policy: z.enum(["reference", "open_judgment"]) }),
  z.strictObject({ ...common, type: z.literal("square_set"), criterion: boundedText(2000, 1), candidate_region: z.array(square).max(64).nullable() }),
  z.strictObject({ ...common, type: z.literal("knight_path"), start_square: square, target_squares: z.array(square).min(1).max(8), minimum_hops: z.number().int().min(0).max(3), maximum_hops: z.number().int().min(0).max(3), hop_rule: z.enum(["at_most", "exact"]), occupancy_rule: z.literal("static_non_capturing") }),
  z.strictObject({ ...common, type: z.literal("choice"), options: z.array(z.strictObject({ id: boundedText(80, 1), text: boundedText(1000, 1) })).min(2).max(12) }),
  z.strictObject({ ...common, type: z.literal("explanation") }),
]).superRefine((value, ctx) => {
  if ((value.type === "choice" && hasDuplicates(value.options.map(option => option.id))) ||
      (value.type === "knight_path" && hasDuplicates(value.target_squares)) ||
      (value.type === "square_set" && value.candidate_region !== null && hasDuplicates(value.candidate_region)))
    ctx.addIssue({ code: "custom", message: "Duplicate exercise choices or squares" });
});
const assessment = z.object({
  outcome: z.enum(["correct", "incorrect", "unrecognized", "needs_self_assessment"]), feedback: z.string(),
  omitted: z.array(square).max(64).optional(), extra: z.array(square).max(64).optional(), outside_region: z.array(square).max(64).optional(),
}).strict();
const pending = z.strictObject({ attempt_id: attemptId, assessment, pending_self_assessment: z.literal(true) })
  .refine(value => ["unrecognized", "needs_self_assessment"].includes(value.assessment.outcome));
const completed = z.strictObject({ attempt_id: attemptId, assessment, rating: z.enum(["correct", "again"]),
  requested_rating: z.enum(["correct", "again"]), persisted: z.literal(true),
  review: z.object({ review_id: z.number().int().positive(), requeue_entry_id: z.number().int().positive().nullable() }).passthrough().nullable(),
});
const specification = studySpecificationSchema.superRefine((value, ctx) => {
  const invalid = (message: string) => ctx.addIssue({ code: "custom", message });
  if (length(value.prompt) > 2000 || length(value.hint) > 2000 || length(value.explanation) > 10000 || length(value.further_analysis ?? "") > 10000) invalid("Exercise text exceeds owner bounds");
  if (value.type === "move_line" && (value.accepted_lines.length < 1 || value.accepted_lines.length > 20 || value.accepted_lines.some(line => line.length < 1 || (value.mode === "single" && line.length !== 1) || line.some(move => !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move))))) invalid("Invalid accepted lines");
  if (value.type === "square_set") {
    const required = new Set(value.required);
    const region = value.candidate_region && new Set(value.candidate_region);
    if (value.required.length > 64 || value.optional.length > 64 || (value.candidate_region?.length ?? 0) > 64 || length(value.criterion) > 2000 ||
        hasDuplicates(value.required) || hasDuplicates(value.optional) || value.optional.some(square => required.has(square)) ||
        (value.candidate_region !== null && (hasDuplicates(value.candidate_region) || [...value.required, ...value.optional].some(square => !region!.has(square))))) invalid("Invalid square projection");
  }
  if (value.type === "knight_path" && (value.target_squares.length < 1 || value.target_squares.length > 8 || value.minimum_hops > value.maximum_hops || hasDuplicates(value.target_squares))) invalid("Invalid knight projection");
  if (value.type === "choice") {
    const optionIds = value.options.map(option => option.id);
    const known = new Set(optionIds);
    if (value.options.length < 2 || value.options.length > 12 || value.options.some(option => !length(option.id) || length(option.id) > 80 || !length(option.text) || length(option.text) > 1000) || !value.correct_option_ids.length || hasDuplicates(optionIds) || hasDuplicates(value.correct_option_ids) || value.correct_option_ids.some(id => !known.has(id))) invalid("Invalid choices");
  }
  if (value.type === "explanation" && length(value.rubric) > 10000) invalid("Invalid rubric");
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
