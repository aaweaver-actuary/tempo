import { z } from "zod";
import { Chess } from "chess.js";

const count = z.number().int().nonnegative();
const color = z.enum(["white", "black"]);
const distribution = z.array(z.object({ depth: z.number().int().positive(), line_count: count }));
const diagnostic = {
  version: z.literal(1), preview_only: z.literal(true), estimate_basis: z.string(),
  repertoire_id: z.string(), graph_generation: count, snapshot_id: z.string().min(1),
};
export const prefixSourceSchema = z.object({
  ...diagnostic,
  lines: z.array(z.object({ id: z.string(), name: z.string(), start_fen: z.string(),
    moves: z.array(z.string()), trained_color: color, saved_depth: z.number().int().positive() })),
  current_depth_distribution: distribution,
});
const metricShape = {
  distinct_cards: count, prefix_cards: count, descendant_decision_cards: count,
  learner_decision_occurrences: count, distinct_learner_decisions: count,
  repeated_decisions_across_cards: count, repeated_decisions_within_cards: count, board_starts: count,
};
const metrics = z.object(metricShape);
const presentation = z.object({
  metrics,
  cards: z.array(z.object({ card_id: z.string(), starting_fen: z.string(), moves: z.array(z.string()),
    trained_color: color, roles: z.array(z.enum(["prefix", "decision"])), line_ids: z.array(z.string()), decision_ids: z.array(z.string()) })),
  steps: z.array(z.object({ repertoire_id: z.string(), line_id: z.string(), decision_index: count,
    segment_kind: z.enum(["prefix", "decision"]), first_decision_index: count, last_decision_index: count,
    decision_fen_keys: z.array(z.string()), card_id: z.string(), parent_card_id: z.string().nullable(),
    decision_fen_key: z.string(), starting_fen: z.string(), moves: z.array(z.string()), trained_color: color })),
});
const signedCount = z.number().int();
const comparison = z.object({
  current: presentation, proposed: presentation,
  delta: z.object({ distinct_cards: signedCount, prefix_cards: signedCount, descendant_decision_cards: signedCount,
    learner_decision_occurrences: signedCount, distinct_learner_decisions: signedCount,
    repeated_decisions_across_cards: signedCount, repeated_decisions_within_cards: signedCount, board_starts: signedCount }),
  additional_starts: count, reduced_starts: count,
  unchanged_card_ids: z.array(z.string()), added_card_ids: z.array(z.string()), removed_card_ids: z.array(z.string()),
});
export const prefixComparisonSchema = z.object({
  ...diagnostic, selected_line_ids: z.array(z.string()), selected_line_count: count,
  status: z.enum(["empty_selection", "changed", "no_change"]),
  depth_configuration_changed: z.boolean(), structure_changed: z.boolean(), current_depth_distribution: distribution,
  line_depths: z.array(z.object({ line_id: z.string(), current_depth: z.number().int().positive(),
    requested_depth: z.number().int().min(1).max(20), current_effective_depth: count, proposed_effective_depth: count })),
  selected: comparison, whole_repertoire: comparison,
});
export type PrefixSource = z.infer<typeof prefixSourceSchema>;
export type PrefixComparison = z.infer<typeof prefixComparisonSchema>;
export type SourceRoute = PrefixSource["lines"][number];
export type RoutePrefix = Pick<SourceRoute, "start_fen" | "trained_color" | "moves">;

// Selection is literal route matching, never graph or position equivalence.
export function exactRouteLines(lines: SourceRoute[], prefix: RoutePrefix): SourceRoute[] {
  return lines.filter(line => line.start_fen === prefix.start_fen && line.trained_color === prefix.trained_color
    && prefix.moves.every((move, index) => line.moves[index] === move)).sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
}

export function routeMovetext(route: Pick<SourceRoute, "start_fen" | "moves">): string {
  try {
    const board = new Chess(route.start_fen);
    const tokens: string[] = [];
    for (const uci of route.moves) {
      const moveNumber = board.fen().split(" ")[5];
      const turn = board.turn();
      const played = board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
      tokens.push(`${turn === "w" ? `${moveNumber}.` : tokens.length === 0 ? `${moveNumber}...` : ""}${played.san}`);
    }
    return tokens.join(" ");
  } catch { return route.moves.join(" "); }
}

export function candidateDepths(text: string): number[] {
  const tokens = text.trim().split(/[\s,]+/);
  if (!text.trim() || tokens.some(token => !/^\d+$/.test(token)))
    throw new Error("Enter one to four integer depths from 1 to 20, separated by commas.");
  const depths = [...new Set(tokens.map(Number))].sort((left, right) => left - right);
  if (depths.length > 4 || depths.some(depth => depth < 1 || depth > 20))
    throw new Error("Enter one to four integer depths from 1 to 20, separated by commas.");
  return depths;
}

export const structuralMetricLabels: Record<keyof PrefixComparison["selected"]["delta"], string> = {
  distinct_cards: "Distinct cards", prefix_cards: "Prefix cards", descendant_decision_cards: "Descendant decision cards",
  learner_decision_occurrences: "Learner decisions per pass", distinct_learner_decisions: "Distinct learner decisions",
  repeated_decisions_across_cards: "Repeated decisions across cards", repeated_decisions_within_cards: "Repeated decisions within cards",
  board_starts: "Board starts",
};
