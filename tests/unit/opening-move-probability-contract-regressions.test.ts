// @vitest-environment node
import { execFileSync } from "node:child_process";
import { expect, it } from "vitest";
import * as z from "zod";
import fixtures from "../fixtures/opening-move-probability/examples.json";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { openingMoveEvidenceSchema, openingMoveEvidenceBundleSchema, positionMoveUniverseSchema,
  fusionRequestSchema, fusedMoveDistributionSchema, moveUniverse, empiricalFrequencies,
  normalizeEvidenceWeights, moveWeightSchema, fuseMoveEvidence, coverageMassSummary } from "../../app/domain/opening-move-probability";

const schemas: Record<string, z.ZodType> = { source: openingMoveEvidenceSchema, bundle: openingMoveEvidenceBundleSchema,
  position: positionMoveUniverseSchema, request: fusionRequestSchema, fused: fusedMoveDistributionSchema };

function change(payload: unknown, path: (string | number)[], value: unknown): unknown {
  const copy: unknown = structuredClone(payload);
  let parent = copy;
  for (const part of path.slice(0, -1)) parent = (parent as Record<string | number, unknown>)[part];
  (parent as Record<string | number, unknown>)[path[path.length - 1]] = value;
  return copy;
}

it("opening_probability_python_typescript_share_valid_and_invalid_contract_corpus", () => {
  const cases: { name: string; schema: string; payload: unknown; valid: boolean }[] = [];
  for (const name of ["explorer", "explorer_other_cohort", "maia_dense", "maia_sparse"] as const)
    cases.push({ name, schema: "source", payload: fixtures[name], valid: true });
  for (const [name, schema] of [["bundle", "bundle"], ["fusion_request", "request"], ["fused", "fused"], ["unavailable", "fused"]] as const)
    cases.push({ name, schema, payload: fixtures[name], valid: true });
  function sourceCase(name: string, source: unknown, path: (string | number)[], value: unknown, valid = false) {
    cases.push({ name, schema: "source", payload: change(source, path, value), valid });
  }
  sourceCase("version", fixtures.explorer, ["contract_version"], 2);
  sourceCase("boolean_version", fixtures.explorer, ["contract_version"], true);
  sourceCase("unknown_field", fixtures.explorer, ["extra"], 1);
  sourceCase("negative", fixtures.maia_dense, ["distribution", "moves", 0, "probability"], -.1);
  sourceCase("string_probability", fixtures.maia_dense, ["distribution", "moves", 0, "probability"], "0.4");
  sourceCase("boolean_probability", fixtures.maia_dense, ["distribution", "moves", 0, "probability"], true);
  sourceCase("mass_overflow", fixtures.maia_dense, ["distribution", "moves", 0, "probability"], .5);
  sourceCase("mass_roundoff", fixtures.maia_dense, ["distribution", "moves", 0, "probability"], .4 + 5e-10, true);
  sourceCase("missing_residual", fixtures.maia_sparse, ["distribution", "unknown_mass"], 0);
  sourceCase("negative_residual", fixtures.maia_sparse, ["distribution", "unknown_mass"], -.2);
  sourceCase("raw_only_numeric", fixtures.explorer, ["distribution"], fixtures.maia_dense.distribution);
  sourceCase("duplicate_raw", fixtures.explorer, ["raw_evidence", "moves"], [fixtures.explorer.raw_evidence.moves[0], fixtures.explorer.raw_evidence.moves[0]]);
  sourceCase("count_denominator", fixtures.explorer, ["raw_evidence", "total_count"], 2);
  for (const count of [true, 1.5, "7", 2 ** 53])
    sourceCase(`count_${String(count)}`, fixtures.explorer, ["raw_evidence", "moves", 0, "count"], count);
  for (const move of ["0000", "e8e6", "E8D7", "garbage"])
    sourceCase(`move_${move}`, fixtures.explorer, ["raw_evidence", "moves", 0, "move_uci"], move);
  sourceCase("missing_table_row", fixtures.maia_dense, ["distribution", "moves"], fixtures.maia_dense.distribution.moves.slice(1));
  sourceCase("invalid_fen", fixtures.maia_dense, ["distribution", "position", "fen_key"], "bad FEN");
  sourceCase("missing_inventory", fixtures.maia_dense, ["distribution", "position", "legal_moves"], fixtures.maia_dense.distribution.position.legal_moves.slice(1));
  sourceCase("inexact_context_integer", fixtures.explorer, ["cohort", "inexact"], 2 ** 53);
  sourceCase("bad_timestamp", fixtures.explorer, ["captured_at"], "yesterday");
  sourceCase("no_timezone", fixtures.explorer, ["captured_at"], "2026-10-10T12:00:00");
  sourceCase("freshness_mismatch", fixtures.explorer, ["freshness", "state"], "stale");
  sourceCase("microseconds_fresh", fixtures.explorer, ["freshness"], {
    as_of: "2026-10-10T12:00:00.000001Z", valid_until: "2026-10-10T12:00:00.000002Z", state: "fresh" }, true);
  sourceCase("microseconds_stale", fixtures.explorer, ["freshness"], {
    as_of: "2026-10-10T12:00:00.000002Z", valid_until: "2026-10-10T12:00:00.000002Z", state: "stale" }, true);
  sourceCase("unknown_expiry", fixtures.explorer, ["freshness"], {
    as_of: "2026-10-10T12:00:00Z", valid_until: null, state: "unknown" }, true);
  sourceCase("provenance_order", fixtures.explorer, ["provenance"], [
    { ...fixtures.explorer.provenance[0], record_id: "zzz-input" }, fixtures.explorer.provenance[0]], true);
  sourceCase("unicode_flags", fixtures.explorer, ["quality", "flags"], ["😀", "\ue000"], true);
  sourceCase("unicode_identifier_length", fixtures.explorer, ["source_version"], "😀".repeat(300), true);
  cases.push({ name: "opaque_context_provenance", schema: "bundle", valid: true,
    payload: change(fixtures.bundle, ["target_context", "provenance"], [{ sequence: 2 }, { sequence: 1 }]) });
  cases.push({ name: "duplicate_sources", schema: "bundle", valid: false,
    payload: change(fixtures.bundle, ["sources"], [...fixtures.bundle.sources, fixtures.bundle.sources[0]]) });
  cases.push({ name: "different_position", schema: "bundle", valid: false,
    payload: change(fixtures.bundle, ["position"], moveUniverse("4k3/8/8/8/8/8/8/4K3 w - - 0 1")) });
  cases.push({ name: "unavailable_with_distribution", schema: "fused", valid: false,
    payload: change(fixtures.unavailable, ["distribution"], fixtures.maia_dense.distribution) });
  for (const position of fixtures.identity_cases) cases.push({ name: position.name, schema: "position", payload: position.position, valid: true });
  const python = JSON.parse(execFileSync(resolvePython(), ["tests/fixtures/opening-move-probability/validate.py"], {
    input: JSON.stringify(cases), encoding: "utf8", env: { ...process.env, PYTHONPATH: "backend" },
  })) as { valid: boolean; normalized?: unknown; serialized?: string }[];
  expect(python).toHaveLength(cases.length);
  for (const [index, item] of cases.entries()) {
    const frontend = schemas[item.schema].safeParse(item.payload);
    expect(frontend.success, item.name).toBe(item.valid);
    expect(python[index].valid, item.name).toBe(item.valid);
    if (frontend.success) expect(frontend.data, item.name).toEqual(python[index].normalized);
  }
});

it("opening_probability_missing_counts_observed_zero_and_raw_only_mass_remain_distinct", () => {
  const evidence = openingMoveEvidenceSchema.parse(fixtures.explorer);
  if (evidence.raw_evidence.kind !== "counts") throw new Error("Expected counts fixture");
  expect(empiricalFrequencies(evidence.raw_evidence)).toEqual({ e8d7: .7, e8d8: 0 });
  expect(evidence.distribution.moves.every(move => move.probability === null)).toBe(true);
  expect(evidence.distribution.unknown_mass).toBe(1);
  expect(empiricalFrequencies({ kind: "counts", total_count: 0, moves: [{ move_uci: evidence.distribution.position.legal_moves[0], count: 0 }] })).toEqual({ e8d7: null });
});

it("opening_probability_raw_helpers_reject_duplicate_observations", () => {
  const move = openingMoveEvidenceSchema.parse(fixtures.explorer).distribution.position.legal_moves[0];
  expect(() => empiricalFrequencies({ kind: "counts", total_count: 10,
    moves: [{ move_uci: move, count: 0 }, { move_uci: move, count: 0 }] })).toThrow("duplicate");
});

it("opening_probability_normalization_keeps_raw_provenance_and_explicit_residual", () => {
  const evidence = openingMoveEvidenceSchema.parse(fixtures.explorer);
  const original = structuredClone(evidence);
  const weights = [{ move_uci: "e8d7", value: 7 }, { move_uci: "e8d8", value: 0 }].map(value => moveWeightSchema.parse(value));
  const options = { evidence_id: "derived-fixture", unknown_mass: .2, method: { id: "explicit-test-weights", version: "1", parameters: { reserve: .2 } } };
  const result = normalizeEvidenceWeights(evidence, weights, options);
  expect(result.distribution.moves.map(move => move.probability)).toEqual([.8, 0, null, null, null]);
  expect(result.raw_evidence).toEqual(evidence.raw_evidence);
  expect(result.provenance).toEqual(evidence.provenance);
  expect(evidence).toEqual(original);
  expect(result.derived_from).toEqual([evidence.evidence_id]);
  expect(openingMoveEvidenceBundleSchema.parse({ position: evidence.distribution.position, target_context: {}, sources: [evidence, result] }).sources).toHaveLength(2);
  expect(() => normalizeEvidenceWeights(evidence, weights, { ...options, evidence_id: evidence.evidence_id })).toThrow("distinct derived");
  expect(() => normalizeEvidenceWeights(evidence, [], options)).toThrow("empty or zero");
  expect(() => normalizeEvidenceWeights(evidence, [moveWeightSchema.parse({ move_uci: "e8d7", value: 0 })], options)).toThrow("empty or zero");
  expect(() => normalizeEvidenceWeights(evidence, weights, { ...options, unknown_mass: 0 })).toThrow();
  expect(() => normalizeEvidenceWeights(evidence, [...weights, ...weights], options)).toThrow("duplicate");
  for (const value of [NaN, Infinity, -Infinity, -.1]) {
    expect(moveWeightSchema.safeParse({ move_uci: "e8d7", value }).success).toBe(false);
    expect(openingMoveEvidenceSchema.safeParse(change(fixtures.maia_dense, ["distribution", "moves", 0, "probability"], value)).success).toBe(false);
  }
});

it("opening_probability_canonical_identity_covers_en_passant_castling_promotion_and_terminal_rejection", () => {
  for (const item of fixtures.identity_cases) expect(moveUniverse(item.fen), item.name).toEqual(item.position);
  expect(() => moveUniverse("7k/6Q1/6K1/8/8/8/8/8 b - - 0 1")).toThrow();
  const castling = moveUniverse(fixtures.identity_cases.find(item => item.name === "castling")!.fen);
  expect(castling.legal_moves).toEqual(expect.arrayContaining(["e1g1", "e1c1"]));
  const promotion = moveUniverse(fixtures.identity_cases.find(item => item.name === "promotion")!.fen);
  expect(promotion.legal_moves).toEqual(expect.arrayContaining(["a7a8q", "a7a8r", "a7a8b", "a7a8n"]));
});

it("opening_probability_sources_coexist_and_set_order_is_deterministic", () => {
  const bundle = openingMoveEvidenceBundleSchema.parse(fixtures.bundle);
  const reordered = structuredClone(fixtures.bundle);
  reordered.sources.reverse();
  reordered.position.legal_moves.reverse();
  for (const source of reordered.sources) {
    source.distribution.moves.reverse(); source.distribution.position.legal_moves.reverse(); source.raw_evidence.moves.reverse();
  }
  expect(openingMoveEvidenceBundleSchema.parse(reordered)).toEqual(bundle);
  expect(bundle.sources.filter(source => source.source_id === "explorer-like")).toHaveLength(2);
  expect(bundle.target_context).toEqual(fixtures.bundle.target_context);
});

it("opening_probability_fusion_preserves_sources_lineage_and_rejects_foreign_contributions", () => {
  const request = fusionRequestSchema.parse(fixtures.fusion_request);
  const original = structuredClone(request);
  const result = fuseMoveEvidence(request, () => fusedMoveDistributionSchema.parse({ ...fixtures.fused, provenance: [] }));
  expect(result.provenance).toEqual(request.evidence.sources[2].provenance);
  expect(result.distribution).toEqual(request.evidence.sources[2].distribution);
  expect(request).toEqual(original);
  fuseMoveEvidence(request, policyInput => {
    policyInput.evidence.sources[0].cohort.policy_mutation = true;
    return fusedMoveDistributionSchema.parse(fixtures.fused);
  });
  expect(request).toEqual(original);
  expect(() => fuseMoveEvidence(request, () => fusedMoveDistributionSchema.parse(change(fixtures.fused, ["contributions", 0, "evidence_id"], "foreign")))).toThrow("Unknown source");
  expect(() => fuseMoveEvidence(request, policyInput => {
    policyInput.evidence.target_context.policy_mutation = true;
    return fusedMoveDistributionSchema.parse({ ...fixtures.fused, target_context: policyInput.evidence.target_context });
  })).toThrow("match its request");
  expect(() => fuseMoveEvidence(request, () => fusedMoveDistributionSchema.parse({ ...fixtures.fused, target_context: {} }))).toThrow("match its request");
  expect(fuseMoveEvidence(request, () => fusedMoveDistributionSchema.parse(fixtures.unavailable)).distribution).toBeNull();
});

it("opening_probability_coverage_keeps_known_uncovered_mass_and_unknown_mass", () => {
  const distribution = openingMoveEvidenceSchema.parse(fixtures.maia_sparse).distribution;
  expect(coverageMassSummary(distribution, new Set(["e8d7"]))).toEqual({ known_covered_mass: .45, known_uncovered_mass: .35, unknown_mass: .2 });
  expect(() => coverageMassSummary(distribution, new Set(["e8e6"]))).toThrow("illegal");
});
