import * as z from "zod";
import { Chess } from "chess.js";

// Pydantic v1 accepts these JSON scalar spellings for booleans and integers.
const pyBool = z.preprocess((value) => {
  if (value === 0 || value === 1) return Boolean(value);
  if (typeof value === "string") {
    const v = value.toLowerCase();
    if (["true", "t", "yes", "y", "on", "1"].includes(v)) return true;
    if (["false", "f", "no", "n", "off", "0"].includes(v)) return false;
  }
  return value;
}, z.boolean());
const pyInt = z.preprocess((value) => {
  if (typeof value === "boolean") return Number(value);
  if (typeof value === "string" && /^\s*[+-]?\d+(?:\.0+)?\s*$/.test(value)) return Number(value);
  return value;
}, z.number().int());
const square = z.string().regex(/^[a-h][1-8]$/);
const boundedText = (max: number, min = 0) => z.string().refine((value) => Array.from(value).length >= min && Array.from(value).length <= max);
const text = { prompt: boundedText(2000, 1), hint: boundedText(2000).default(""), explanation: boundedText(10000).default(""), further_analysis: boundedText(10000).optional() };
export const studySpecificationSchema = z.discriminatedUnion("type", [
  z.strictObject({ ...text, type: z.literal("move_line"), grading_policy: z.enum(["reference", "open_judgment"]).default("reference"),
    mode: z.enum(["single", "stepwise_line"]).default("single"), accepted_lines: z.array(z.array(z.string())).min(1).max(20) }),
  z.strictObject({ ...text, type: z.literal("square_set"), required: z.array(z.string()).max(64).default([]), optional: z.array(z.string()).max(64).default([]),
    candidate_region: z.array(z.string()).nullable().default(null), criterion: boundedText(2000, 1) }),
  z.strictObject({ ...text, type: z.literal("knight_path"), start_square: z.string(), target_squares: z.array(z.string()).min(1).max(8),
    minimum_hops: pyInt.pipe(z.number().min(0).max(3)).default(1), maximum_hops: pyInt.pipe(z.number().min(0).max(3)).default(2),
    hop_rule: z.enum(["at_most", "exact"]).default("at_most"), occupancy_rule: z.literal("static_non_capturing").default("static_non_capturing") }),
  z.strictObject({ ...text, type: z.literal("choice"), options: z.array(z.strictObject({ id: boundedText(80, 1), text: boundedText(1000, 1) })).min(2).max(12),
    correct_option_ids: z.array(z.string()).min(1) }),
  z.strictObject({ ...text, type: z.literal("explanation"), rubric: boundedText(10000, 1) }),
]);
export const studyAnswerSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("move_line"), moves: z.array(z.string()).min(1).max(40) }),
  z.strictObject({ type: z.literal("square_set"), squares: z.array(z.string()).max(64) }),
  z.strictObject({ type: z.literal("knight_path"), reachable: pyBool, path: z.array(z.string()).max(4).default([]) }),
  z.strictObject({ type: z.literal("choice"), option_ids: z.array(z.string()).min(1).max(12), displayed_order: z.array(z.string()).max(12).default([]) }),
  z.strictObject({ type: z.literal("explanation"), text: boundedText(10000).default(""), ready: pyBool.default(true) }),
]);
export const studySnapshotSchema = z.strictObject({
  schema_version: z.literal(1), grader_version: z.literal(1), exercise_id: z.string(),
  revision: z.number().int().positive(), fen: z.string(), specification: studySpecificationSchema,
}).refine((snapshot) => validateStudyExercise(snapshot.specification, snapshot.fen), "Invalid study exercise");

export type StudySpecification = z.infer<typeof studySpecificationSchema>;
export type StudyAnswer = z.infer<typeof studyAnswerSchema>;
export type StudySnapshot = z.infer<typeof studySnapshotSchema>;
export type StudyAssessment = { outcome: "correct" | "incorrect" | "unrecognized" | "needs_self_assessment" | "invalid_submission";
  feedback: string; omitted?: string[]; extra?: string[]; outside_region?: string[] };

// python-chess fills omitted FEN fields and treats fullmove zero as one.
// Normalize only the temporary board input; keep the frozen snapshot unchanged.
function boardFen(fen: string): string {
  const fields = fen.trim().split(/\s+/);
  const defaults = ["", "w", "-", "-", "0", "1"];
  for (let index = fields.length; index < defaults.length; index++) fields.push(defaults[index]);
  if (/^[+-]?0+$/.test(fields[5])) fields[5] = "1";
  return fields.join(" ");
}

const boardSquares = Array.from({ length: 64 }, (_, index) => `${"abcdefgh"[index % 8]}${Math.floor(index / 8) + 1}`);
function knightAttack(from: string, target: string) {
  const dx = Math.abs(from.charCodeAt(0) - target.charCodeAt(0));
  const dy = Math.abs(Number(from[1]) - Number(target[1]));
  return dx * dy === 2 && dx + dy === 3;
}
/** Validate the authored v1 exercise before evaluating a frozen snapshot. */
export function validateStudyExercise(specification: StudySpecification, fen: string): boolean {
  try {
    const board = new Chess(boardFen(fen));
    const pieces = board.board().flat().filter((piece) => piece !== null);
    for (const color of ["w", "b"] as const) {
      const owned = pieces.filter((piece) => piece.color === color);
      if (owned.length > 16 || owned.filter((piece) => piece.type === "k").length !== 1 || owned.filter((piece) => piece.type === "p").length > 8) return false;
      if (owned.some((piece) => piece.type === "p" && (piece.square[1] === "1" || piece.square[1] === "8"))) return false;
      const king = owned.find((piece) => piece.type === "k")!;
      if (color !== board.turn() && board.isAttacked(king.square, board.turn())) return false;
    }
    const [, turn, rights, ep] = boardFen(fen).split(/\s+/);
    for (const [right, king, rook, color] of [["K", "e1", "h1", "w"], ["Q", "e1", "a1", "w"], ["k", "e8", "h8", "b"], ["q", "e8", "a8", "b"]] as const) {
      if (rights?.includes(right) && (board.get(king)?.type !== "k" || board.get(king)?.color !== color || board.get(rook)?.type !== "r" || board.get(rook)?.color !== color)) return false;
    }
    if (ep && ep !== "-") {
      const rank = turn === "w" ? "6" : "3", pawnRank = turn === "w" ? "5" : "4", startRank = turn === "w" ? "7" : "2";
      const pawn = board.get(`${ep[0]}${pawnRank}` as Parameters<Chess["get"]>[0]);
      if (ep[1] !== rank || board.get(ep as Parameters<Chess["get"]>[0]) || board.get(`${ep[0]}${startRank}` as Parameters<Chess["get"]>[0]) || pawn?.type !== "p" || pawn.color === turn) return false;
    }
    const ourKing = pieces.find((piece) => piece.type === "k" && piece.color === board.turn())!;
    const enemy = board.turn() === "w" ? "b" : "w";
    const checkers = board.attackers(ourKing.square, enemy);
    if (checkers.length > 2) return false;
    if (checkers.length === 2) {
      const [a, b] = checkers;
      const dx1 = a.charCodeAt(0) - ourKing.square.charCodeAt(0), dy1 = Number(a[1]) - Number(ourKing.square[1]);
      const dx2 = b.charCodeAt(0) - ourKing.square.charCodeAt(0), dy2 = Number(b[1]) - Number(ourKing.square[1]);
      if (dx1 * dy2 === dx2 * dy1 && (dx1 === 0 || dy1 === 0 || Math.abs(dx1) === Math.abs(dy1))) return false;
    }
    if (ep && ep !== "-" && checkers.length) {
      if (checkers.length > 1) return false;
      const to = `${ep[0]}${turn === "w" ? "5" : "4"}` as Parameters<Chess["get"]>[0];
      if (checkers[0] !== to) {
        const prior = new Chess(boardFen(fen));
        const pawn = prior.remove(to)!;
        prior.put(pawn, `${ep[0]}${turn === "w" ? "7" : "2"}` as Parameters<Chess["get"]>[0]);
        if (prior.isAttacked(ourKing.square, enemy)) return false;
      }
    }
    const unique = (values: string[]) => new Set(values).size === values.length;
    const squares = (values: string[]) => values.every((value) => square.safeParse(value).success);
    if (specification.type === "move_line") {
      for (const line of specification.accepted_lines) {
        if (!line.length || (specification.mode === "single" && line.length !== 1)) return false;
        const position = new Chess(boardFen(fen));
        for (const move of line) {
          if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) return false;
          position.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
        }
      }
    } else if (specification.type === "square_set") {
      const all = [...specification.required, ...specification.optional];
      if (!squares(all) || !unique(all)) return false;
      const region = specification.candidate_region;
      if (region !== null && (!squares(region) || !unique(region) || all.some((value) => !region.includes(value)))) return false;
    } else if (specification.type === "knight_path") {
      if (!squares([specification.start_square, ...specification.target_squares]) || !unique(specification.target_squares) || specification.minimum_hops > specification.maximum_hops) return false;
      if (board.get(specification.start_square as Parameters<Chess["get"]>[0])?.type !== "n") return false;
    } else if (specification.type === "choice") {
      const ids = specification.options.map((option) => option.id);
      if (!unique(ids) || !unique(specification.correct_option_ids) || specification.correct_option_ids.some((id) => !ids.includes(id))) return false;
    }
    return true;
  } catch { return false; }
}

export function availableKnightRoutes(specification: Extract<StudySpecification, { type: "knight_path" }>, fen: string): string[][] {
  const board = new Chess(boardFen(fen));
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
    const board = new Chess(boardFen(fen));
    try { for (const move of answer.moves) {
      if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move) && move !== "0000") return { outcome: "invalid_submission", feedback: "Use legal UCI moves" };
      board.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
    } }
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
    for (const value of answer.squares) if (!square.safeParse(value).success) return { outcome: "invalid_submission", feedback: `Invalid square: ${value}` };
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
