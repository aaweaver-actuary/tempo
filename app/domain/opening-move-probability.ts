import * as z from "zod";
import { Chess } from "chess.js";
import { canonicalFenKey } from "../utils/canonical-line";
import { fenKeySchema, uciMoveSchema } from "./schemas/primitives";

export const PROBABILITY_TOLERANCE = 1e-9;
const identifier = z.string().min(1).refine(value => [...value].length <= 512, "Identifier exceeds 512 Unicode code points");
const uci = z.string().regex(/^[a-h][1-8][a-h][1-8][qrbn]?$/).pipe(uciMoveSchema);
const nonnegative = z.number().nonnegative().refine(value => !Number.isInteger(value) || Number.isSafeInteger(value),
  "JSON integers must be exactly representable");
const timestamp = z.iso.datetime({ offset: true }).regex(
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/,
).refine(value => !value.startsWith("0000-"), "Calendar year must be between 1 and 9999");
const flags = z.array(identifier).transform(values => [...new Set(values)].sort(compare));
const jsonObject = z.record(z.string(), z.json()).superRefine((value, context) => {
  function check(item: unknown) {
    if (typeof item === "number" && Number.isInteger(item) && !Number.isSafeInteger(item))
      context.addIssue({ code: "custom", message: "JSON integers must be exactly representable" });
    if (Array.isArray(item)) item.forEach(check);
    else if (item !== null && typeof item === "object") Object.values(item).forEach(check);
  }
  check(value);
});

export const positionMoveUniverseSchema = z.strictObject({
  fen_key: z.string().refine(value => value === value.trim()).pipe(fenKeySchema),
  legal_moves: z.array(uci),
}).superRefine((position, context) => {
  let board: Chess;
  try { board = new Chess(`${position.fen_key} 0 1`); }
  catch { context.addIssue({ code: "custom", message: "Invalid position FEN" }); return; }
  if (canonicalFenKey(board.fen()) !== position.fen_key)
    context.addIssue({ code: "custom", message: "Expected Tempo's canonical four-field FEN key" });
  const expectedMoves = board.moves({ verbose: true }).map(move => `${move.from}${move.to}${move.promotion ?? ""}`).sort();
  if (!expectedMoves.length)
    context.addIssue({ code: "custom", message: "Position has no legal opponent moves" });
  if (JSON.stringify([...position.legal_moves].sort()) !== JSON.stringify(expectedMoves))
    context.addIssue({ code: "custom", message: "Legal inventory must contain every legal move exactly once" });
}).transform(position => ({ ...position, legal_moves: [...position.legal_moves].sort() }));
export type PositionMoveUniverse = z.infer<typeof positionMoveUniverseSchema>;

export function moveUniverse(fen: string): PositionMoveUniverse {
  const board = new Chess(fen.split(/\s+/).length === 4 ? `${fen} 0 1` : fen);
  return positionMoveUniverseSchema.parse({ fen_key: canonicalFenKey(board.fen()),
    legal_moves: board.moves({ verbose: true }).map(move => `${move.from}${move.to}${move.promotion ?? ""}`) });
}

export const moveProbabilityDistributionSchema = z.strictObject({
  contract_version: z.literal(1).default(1), position: positionMoveUniverseSchema,
  moves: z.array(z.strictObject({ move_uci: uci, probability: nonnegative.max(1 + PROBABILITY_TOLERANCE).nullable() })),
  unknown_mass: nonnegative.max(1),
}).superRefine((distribution, context) => {
  if (JSON.stringify(distribution.moves.map(move => move.move_uci).sort()) !== JSON.stringify(distribution.position.legal_moves))
    context.addIssue({ code: "custom", message: "Probability table must contain every legal move exactly once" });
  const knownMass = distribution.moves.reduce((total, move) => total + (move.probability ?? 0), 0);
  if (knownMass > 1 + PROBABILITY_TOLERANCE || Math.abs(knownMass + distribution.unknown_mass - 1) > PROBABILITY_TOLERANCE)
    context.addIssue({ code: "custom", message: "Known and unknown probability mass must total one" });
  if (distribution.moves.some(move => move.probability === null) && distribution.unknown_mass <= 0)
    context.addIssue({ code: "custom", message: "Unassigned moves require positive unknown mass" });
}).transform(distribution => ({ ...distribution, moves: [...distribution.moves].sort((left, right) => compare(left.move_uci, right.move_uci)) }));
export type MoveProbabilityDistribution = z.infer<typeof moveProbabilityDistributionSchema>;

export function unknownDistribution(position: PositionMoveUniverse): MoveProbabilityDistribution {
  return moveProbabilityDistributionSchema.parse({ position, unknown_mass: 1,
    moves: position.legal_moves.map(move_uci => ({ move_uci, probability: null })) });
}

export const countEvidenceSchema = z.strictObject({ kind: z.literal("counts"), total_count: z.number().int().nonnegative(),
  moves: z.array(z.strictObject({ move_uci: uci, count: z.number().int().nonnegative() })),
}).superRefine((evidence, context) => {
  if (new Set(evidence.moves.map(move => move.move_uci)).size !== evidence.moves.length)
    context.addIssue({ code: "custom", message: "Raw evidence contains duplicate moves" });
  // Subtract to avoid overflow/rounding when malformed counts exceed the denominator.
  let remaining = evidence.total_count;
  for (const move of evidence.moves) {
    remaining -= move.count;
    if (remaining < 0) { context.addIssue({ code: "custom", message: "Reported counts exceed the sample denominator" }); break; }
  }
}).transform(evidence => ({ ...evidence, moves: [...evidence.moves].sort((left, right) => compare(left.move_uci, right.move_uci)) }));
export type CountEvidence = z.infer<typeof countEvidenceSchema>;
export const moveWeightSchema = z.strictObject({ move_uci: uci, value: nonnegative });
export type MoveWeight = z.infer<typeof moveWeightSchema>;
const scoreEvidenceSchema = z.strictObject({ kind: z.literal("scores"), basis: z.enum(["probability", "weight"]),
  moves: z.array(moveWeightSchema),
}).superRefine((evidence, context) => {
  if (new Set(evidence.moves.map(move => move.move_uci)).size !== evidence.moves.length)
    context.addIssue({ code: "custom", message: "Raw evidence contains duplicate moves" });
  if (evidence.basis === "probability" && evidence.moves.reduce((total, move) => total + move.value, 0) > 1 + PROBABILITY_TOLERANCE)
    context.addIssue({ code: "custom", message: "Raw probability mass cannot exceed one" });
}).transform(evidence => ({ ...evidence, moves: [...evidence.moves].sort((left, right) => compare(left.move_uci, right.move_uci)) }));

export const methodDescriptorSchema = z.strictObject({ id: identifier, version: identifier, parameters: jsonObject });
export type MethodDescriptor = z.infer<typeof methodDescriptorSchema>;
const provenanceRecordSchema = z.strictObject({ record_id: identifier, input_fingerprint: identifier,
  references: flags, limitations: flags });
const evidenceQualitySchema = z.strictObject({ effective_sample_size: nonnegative.nullable(), flags,
  uncertainty: jsonObject.nullable() });
const evidenceFreshnessSchema = z.strictObject({ as_of: timestamp, valid_until: timestamp.nullable(),
  state: z.enum(["fresh", "stale", "unknown"]),
}).superRefine((freshness, context) => {
  if (!timestamp.safeParse(freshness.as_of).success ||
      (freshness.valid_until !== null && !timestamp.safeParse(freshness.valid_until).success)) return;
  const expectedState = freshness.valid_until === null ? "unknown" :
    compareTimestamps(freshness.as_of, freshness.valid_until) >= 0 ? "stale" : "fresh";
  if (freshness.state !== expectedState)
    context.addIssue({ code: "custom", message: "Freshness must match the supplied clock and expiry" });
});

export const openingMoveEvidenceSchema = z.strictObject({
  contract_version: z.literal(1).default(1), evidence_id: identifier, source_id: identifier,
  derived_from: flags.default([]),
  source_generation: identifier, source_version: identifier.nullable(), cohort: jsonObject,
  raw_evidence: z.discriminatedUnion("kind", [countEvidenceSchema, scoreEvidenceSchema]),
  distribution: moveProbabilityDistributionSchema, normalization: methodDescriptorSchema.nullable(),
  source_timestamp: timestamp.nullable(), captured_at: timestamp, freshness: evidenceFreshnessSchema,
  quality: evidenceQualitySchema, provenance: z.array(provenanceRecordSchema).min(1).transform(orderProvenance),
}).superRefine((evidence, context) => {
  if (evidence.derived_from.includes(evidence.evidence_id))
    context.addIssue({ code: "custom", message: "Evidence cannot derive from itself" });
  const rawMoves = evidence.raw_evidence.moves.map(move => move.move_uci);
  if (new Set(rawMoves).size !== rawMoves.length || rawMoves.some(move => !evidence.distribution.position.legal_moves.includes(move)))
    context.addIssue({ code: "custom", message: "Raw evidence contains duplicate or illegal moves" });
  if (evidence.normalization === null && (evidence.distribution.unknown_mass !== 1 || evidence.distribution.moves.some(move => move.probability !== null)))
    context.addIssue({ code: "custom", message: "Raw-only evidence cannot assign predictive probabilities" });
});
export type OpeningMoveEvidence = z.infer<typeof openingMoveEvidenceSchema>;

export const openingMoveEvidenceBundleSchema = z.strictObject({ contract_version: z.literal(1).default(1),
  position: positionMoveUniverseSchema, target_context: jsonObject, sources: z.array(openingMoveEvidenceSchema).min(1),
}).superRefine((bundle, context) => {
  if (new Set(bundle.sources.map(source => source.evidence_id)).size !== bundle.sources.length)
    context.addIssue({ code: "custom", message: "Duplicate source evidence ID" });
  if (bundle.sources.some(source => !samePosition(source.distribution.position, bundle.position)))
    context.addIssue({ code: "custom", message: "Source positions must match the bundle" });
}).transform(bundle => ({ ...bundle, sources: [...bundle.sources].sort((left, right) => compare(left.evidence_id, right.evidence_id)) }));
export type OpeningMoveEvidenceBundle = z.infer<typeof openingMoveEvidenceBundleSchema>;
export const fusionRequestSchema = z.strictObject({ contract_version: z.literal(1).default(1),
  evidence: openingMoveEvidenceBundleSchema, policy: methodDescriptorSchema });
export type FusionRequest = z.infer<typeof fusionRequestSchema>;

export const fusedMoveDistributionSchema = z.strictObject({ contract_version: z.literal(1).default(1),
  position: positionMoveUniverseSchema, target_context: jsonObject, policy: methodDescriptorSchema,
  status: z.enum(["available", "unavailable"]), distribution: moveProbabilityDistributionSchema.nullable(),
  unavailable_reason: identifier.nullable(), contributions: z.array(z.strictObject({ evidence_id: identifier,
    weight: nonnegative.nullable(), effective_sample_size: nonnegative.nullable() })),
  freshness: evidenceFreshnessSchema, quality: evidenceQualitySchema, provenance: z.array(provenanceRecordSchema).transform(orderProvenance),
}).superRefine((result, context) => {
  if (result.status === "available") {
    if (result.distribution === null || !samePosition(result.distribution.position, result.position) ||
        result.unavailable_reason !== null || !result.contributions.length || !result.distribution.moves.some(move => (move.probability ?? 0) > 0))
      context.addIssue({ code: "custom", message: "Available fusion requires assigned mass and source contributions" });
  } else if (result.distribution !== null || result.unavailable_reason === null)
    context.addIssue({ code: "custom", message: "Unavailable fusion requires a reason and no distribution" });
  if (new Set(result.contributions.map(item => item.evidence_id)).size !== result.contributions.length)
    context.addIssue({ code: "custom", message: "Duplicate source contribution" });
}).transform(result => ({ ...result, contributions: [...result.contributions].sort((left, right) => compare(left.evidence_id, right.evidence_id)) }));
export type FusedMoveDistribution = z.infer<typeof fusedMoveDistributionSchema>;
export type OpeningMoveFusionPolicy = (request: FusionRequest) => FusedMoveDistribution;

export function fuseMoveEvidence(request: FusionRequest, policy: OpeningMoveFusionPolicy): FusedMoveDistribution {
  const checkedRequest = fusionRequestSchema.parse(structuredClone(request));
  const result = fusedMoveDistributionSchema.parse(policy(fusionRequestSchema.parse(structuredClone(checkedRequest))));
  if (!samePosition(result.position, checkedRequest.evidence.position) ||
      stableJson(result.target_context) !== stableJson(checkedRequest.evidence.target_context) || stableJson(result.policy) !== stableJson(checkedRequest.policy))
    throw new Error("Fusion result does not match its request");
  const sourceById = new Map(checkedRequest.evidence.sources.map(source => [source.evidence_id, source]));
  if (result.contributions.some(item => !sourceById.has(item.evidence_id))) throw new Error("Unknown source contribution");
  const lineage = result.contributions.flatMap(item => sourceById.get(item.evidence_id)!.provenance);
  const distinctRecords = new Map([...lineage, ...result.provenance].map(record => [stableJson(record), record]));
  return fusedMoveDistributionSchema.parse({ ...result, provenance: [...distinctRecords.values()] });
}

export function normalizeEvidenceWeights(evidence: OpeningMoveEvidence, weights: readonly MoveWeight[],
  options: { evidence_id: string; unknown_mass: number; method: MethodDescriptor }): OpeningMoveEvidence {
  const checkedEvidence = openingMoveEvidenceSchema.parse(evidence);
  const checkedWeights = weights.map(weight => moveWeightSchema.parse(weight));
  if (options.evidence_id === checkedEvidence.evidence_id || checkedEvidence.derived_from.includes(options.evidence_id))
    throw new Error("Normalization requires a distinct derived evidence ID");
  const legalMoves = checkedEvidence.distribution.position.legal_moves;
  const weightsByMove = new Map(checkedWeights.map(weight => [weight.move_uci, weight.value]));
  if (weightsByMove.size !== checkedWeights.length || checkedWeights.some(weight => !legalMoves.includes(weight.move_uci)))
    throw new Error("Weights contain duplicate or illegal moves");
  const largestWeight = Math.max(...weightsByMove.values());
  if (!checkedWeights.length || largestWeight <= 0) throw new Error("Cannot normalize empty or zero weights");
  const unknownMass = nonnegative.max(1).parse(options.unknown_mass);
  const scaledTotal = [...weightsByMove.values()].reduce((total, value) => total + value / largestWeight, 0);
  const distribution = moveProbabilityDistributionSchema.parse({ position: checkedEvidence.distribution.position,
    unknown_mass: unknownMass, moves: legalMoves.map(move_uci => ({ move_uci,
      probability: weightsByMove.has(move_uci) ? (weightsByMove.get(move_uci)! / largestWeight) / scaledTotal * (1 - unknownMass) : null })) });
  return openingMoveEvidenceSchema.parse({ ...checkedEvidence, evidence_id: options.evidence_id,
    derived_from: [...checkedEvidence.derived_from, checkedEvidence.evidence_id], distribution, normalization: options.method });
}

export function empiricalFrequencies(evidence: CountEvidence): Record<string, number | null> {
  const checkedEvidence = countEvidenceSchema.parse(evidence);
  return Object.fromEntries([...checkedEvidence.moves].sort((left, right) => compare(left.move_uci, right.move_uci))
    .map(move => [move.move_uci, checkedEvidence.total_count ? move.count / checkedEvidence.total_count : null]));
}

export function coverageMassSummary(distribution: MoveProbabilityDistribution, coveredMoves: ReadonlySet<string>) {
  const checkedDistribution = moveProbabilityDistributionSchema.parse(distribution);
  if ([...coveredMoves].some(move => !checkedDistribution.position.legal_moves.some(legalMove => legalMove === move)))
    throw new Error("Coverage contains illegal moves");
  return { known_covered_mass: checkedDistribution.moves.reduce((total, move) => total + (coveredMoves.has(move.move_uci) ? move.probability ?? 0 : 0), 0),
    known_uncovered_mass: checkedDistribution.moves.reduce((total, move) => total + (!coveredMoves.has(move.move_uci) ? move.probability ?? 0 : 0), 0),
    unknown_mass: checkedDistribution.unknown_mass };
}

function compare(left: string, right: string): number {
  const leftCodepoints = [...left].map(character => character.codePointAt(0)!);
  const rightCodepoints = [...right].map(character => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(leftCodepoints.length, rightCodepoints.length); index++) {
    if (leftCodepoints[index] !== rightCodepoints[index]) return leftCodepoints[index] - rightCodepoints[index];
  }
  return leftCodepoints.length - rightCodepoints.length;
}
function compareSequence(left: readonly string[], right: readonly string[]): number {
  for (let index = 0; index < Math.min(left.length, right.length); index++) {
    const comparison = compare(left[index], right[index]);
    if (comparison) return comparison;
  }
  return left.length - right.length;
}
function orderProvenance(records: z.infer<typeof provenanceRecordSchema>[]) {
  return [...records].sort((left, right) => compare(left.record_id, right.record_id) ||
    compare(left.input_fingerprint, right.input_fingerprint) || compareSequence(left.references, right.references) ||
    compareSequence(left.limitations, right.limitations));
}
function samePosition(left: PositionMoveUniverse, right: PositionMoveUniverse): boolean {
  return left.fen_key === right.fen_key && JSON.stringify(left.legal_moves) === JSON.stringify(right.legal_moves);
}
function compareTimestamps(left: string, right: string): number {
  // Date truncates microseconds: retain them for the expiry boundary.
  function microseconds(value: string) {
    const fraction = value.match(/\.(\d+)/)?.[1] ?? "";
    return BigInt(Date.parse(value)) * BigInt(1000) + BigInt(fraction.padEnd(6, "0").slice(3));
  }
  const leftTime = microseconds(left), rightTime = microseconds(right);
  return leftTime < rightTime ? -1 : leftTime > rightTime ? 1 : 0;
}
function stableJson(value: unknown): string {
  function order(item: unknown): unknown {
    if (Array.isArray(item)) return item.map(order);
    if (item !== null && typeof item === "object") return Object.fromEntries(Object.entries(item)
      .sort(([left], [right]) => compare(left, right)).map(([key, child]) => [key, order(child)]));
    return item;
  }
  return JSON.stringify(order(value));
}
