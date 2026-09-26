import * as z from "zod";
import { Chess } from "chess.js";

const square = z.string().regex(/^[a-h][1-8]$/);
const text = { prompt: z.string().min(1), hint: z.string(), explanation: z.string(), further_analysis: z.string().optional() };
export const studySpecificationSchema = z.discriminatedUnion("type", [
  z.strictObject({ ...text, type: z.literal("move_line"), grading_policy: z.enum(["reference", "open_judgment"]),
    mode: z.enum(["single", "stepwise_line"]), accepted_lines: z.array(z.array(z.string())) }),
  z.strictObject({ ...text, type: z.literal("square_set"), required: z.array(square), optional: z.array(square),
    candidate_region: z.array(square).nullable(), criterion: z.string().min(1) }),
  z.strictObject({ ...text, type: z.literal("knight_path"), start_square: square, target_squares: z.array(square),
    minimum_hops: z.number().int().min(0).max(3), maximum_hops: z.number().int().min(0).max(3),
    hop_rule: z.enum(["at_most", "exact"]), occupancy_rule: z.literal("static_non_capturing") }),
  z.strictObject({ ...text, type: z.literal("choice"), options: z.array(z.strictObject({ id: z.string(), text: z.string() })),
    correct_option_ids: z.array(z.string()) }),
  z.strictObject({ ...text, type: z.literal("explanation"), rubric: z.string().min(1) }),
]);
export const studyAnswerSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("move_line"), moves: z.array(z.string()) }),
  z.strictObject({ type: z.literal("square_set"), squares: z.array(square) }),
  z.strictObject({ type: z.literal("knight_path"), reachable: z.boolean(), path: z.array(square) }),
  z.strictObject({ type: z.literal("choice"), option_ids: z.array(z.string()), displayed_order: z.array(z.string()) }),
  z.strictObject({ type: z.literal("explanation"), text: z.string(), ready: z.boolean() }),
]);
export const studySnapshotSchema = z.strictObject({
  schema_version: z.literal(1), grader_version: z.literal(1), exercise_id: z.string(),
  revision: z.number().int().positive(), fen: z.string(), specification: studySpecificationSchema,
});

export type StudySpecification = z.infer<typeof studySpecificationSchema>;
export type StudyAnswer = z.infer<typeof studyAnswerSchema>;
export type StudySnapshot = z.infer<typeof studySnapshotSchema>;
export type StudyAssessment = { outcome: "correct" | "incorrect" | "unrecognized" | "needs_self_assessment" | "invalid_submission";
  feedback: string; omitted?: string[]; extra?: string[]; outside_region?: string[] };

const boardSquares = Array.from({ length: 64 }, (_, index) => `${"abcdefgh"[index % 8]}${Math.floor(index / 8) + 1}`);
function knightAttack(from: string, target: string) {
  const dx = Math.abs(from.charCodeAt(0) - target.charCodeAt(0));
  const dy = Math.abs(Number(from[1]) - Number(target[1]));
  return dx * dy === 2 && dx + dy === 3;
}
export function availableKnightRoutes(specification: Extract<StudySpecification, { type: "knight_path" }>, fen: string): string[][] {
  const board = new Chess(fen);
  const routes: string[][] = [];
  const visit = (position: string, path: string[]) => {
    const hops = path.length - 1;
    const eligible = specification.hop_rule === "exact" ? hops === specification.maximum_hops : hops >= specification.minimum_hops;
    if (eligible && specification.target_squares.every((target) => knightAttack(position, target))) routes.push(path);
    if (hops >= specification.maximum_hops) return;
    for (const target of boardSquares) {
      if (!knightAttack(position, target)) continue;
      if (target !== specification.start_square && board.get(target as Parameters<Chess["get"]>[0])) continue;
      visit(target, [...path, target]);
    }
  };
  visit(specification.start_square, [specification.start_square]);
  return routes;
}

export function evaluateStudyAnswer(specification: StudySpecification, answer: StudyAnswer, fen: string): StudyAssessment {
  if (specification.type !== answer.type) return { outcome: "invalid_submission", feedback: "Answer type does not match this exercise" };
  if (specification.type === "move_line" && answer.type === "move_line") {
    const board = new Chess(fen);
    try { for (const move of answer.moves) board.move(move); }
    catch { return { outcome: "invalid_submission", feedback: "The submitted move is illegal" }; }
    if (specification.mode === "single" && answer.moves.length !== 1)
      return { outcome: "invalid_submission", feedback: "Submit one move" };
    if (specification.accepted_lines.some((line) => JSON.stringify(line) === JSON.stringify(answer.moves)))
      return { outcome: "correct", feedback: "Matches an authored answer" };
    return specification.grading_policy === "open_judgment"
      ? { outcome: "unrecognized", feedback: "Legal move outside the authored reference answers; assess after reveal" }
      : { outcome: "incorrect", feedback: "Does not match an accepted reference continuation" };
  }
  if (specification.type === "square_set" && answer.type === "square_set") {
    const chosen = new Set(answer.squares);
    if (chosen.size !== answer.squares.length) return { outcome: "invalid_submission", feedback: "Select each square once" };
    const accepted = new Set([...specification.required, ...specification.optional]);
    const omitted = specification.required.filter((value) => !chosen.has(value)).sort();
    const extra = answer.squares.filter((value) => !accepted.has(value)).sort();
    const outside_region = specification.candidate_region ? answer.squares.filter((value) => !specification.candidate_region!.includes(value)).sort() : [];
    return { outcome: omitted.length || extra.length || outside_region.length ? "incorrect" : "correct",
      feedback: "Square selection assessed against the authored criterion", omitted, extra, outside_region };
  }
  if (specification.type === "knight_path" && answer.type === "knight_path") {
    const routes = availableKnightRoutes(specification, fen);
    if (!answer.reachable) {
      if (answer.path.length) return { outcome: "invalid_submission", feedback: "A no answer cannot include a route" };
      return { outcome: routes.length ? "incorrect" : "correct", feedback: "Bounded static knight reachability checked" };
    }
    return routes.some((route) => JSON.stringify(route) === JSON.stringify(answer.path))
      ? { outcome: "correct", feedback: "Valid static knight route" }
      : { outcome: "incorrect", feedback: "The route does not meet the stated geometric and occupancy rules" };
  }
  if (specification.type === "choice" && answer.type === "choice") {
    const known = new Set(specification.options.map((option) => option.id));
    if (new Set(answer.option_ids).size !== answer.option_ids.length || answer.option_ids.some((id) => !known.has(id)))
      return { outcome: "invalid_submission", feedback: "Unknown or duplicate choice ID" };
    if (answer.displayed_order.length && (new Set(answer.displayed_order).size !== known.size || answer.displayed_order.some((id) => !known.has(id))))
      return { outcome: "invalid_submission", feedback: "Displayed choice order is incomplete" };
    return { outcome: answer.option_ids.length === specification.correct_option_ids.length && answer.option_ids.every((id) => specification.correct_option_ids.includes(id)) ? "correct" : "incorrect",
      feedback: "Choice assessed against authored option IDs" };
  }
  if (specification.type === "explanation" && answer.type === "explanation")
    return answer.ready ? { outcome: "needs_self_assessment", feedback: "Compare your answer with the authored rubric" }
      : { outcome: "invalid_submission", feedback: "Commit your response before revealing the rubric" };
  return { outcome: "invalid_submission", feedback: "Unsupported answer" };
}
