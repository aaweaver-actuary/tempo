// @vitest-environment node
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { Chess } from "chess.js";
import { expect, it, vi } from "vitest";
import fixtures from "../fixtures/maia-probability/examples.json";
import manifest from "../../backend/app/maia_probability_manifest.json";
import forwardVocabulary from "../../app/lib/all_moves_maia3.json";
import reverseVocabulary from "../../app/lib/all_moves_maia3_reversed.json";
import { openingMoveEvidenceSchema } from "../../app/domain/opening-move-probability";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { createMaiaDistributionAdapter, decodeMaiaLogits, inferMaiaDistribution, loadMaiaAssets,
  prepareMaiaPosition, validateMaiaRatings, verifyMaiaAssets } from "../../scripts/maia-inference.mjs";

const ratings = { side_to_move_rating: 1500, other_player_rating: 1600 };
const vocabularySize = Object.keys(forwardVocabulary).length;
function requestFor(fixture = fixtures.cases[0]) {
  return { position: structuredClone(fixture.position), ratings: { ...ratings },
    expected_side_to_move: fixture.position.fen_key.split(" ")[1] === "w" ? "white" : "black" };
}
function adapter(runLogits = vi.fn(async () => new Float32Array(vocabularySize))) {
  return createMaiaDistributionAdapter({ model: manifest, moveIndex: forwardVocabulary,
    reversedMoveIndex: reverseVocabulary, runLogits });
}

it("maia_source_rejects_changed_vocabulary_runtime_and_schema_provenance", () => {
  const moveBytes = readFileSync("app/lib/all_moves_maia3.json");
  const reverseBytes = readFileSync("app/lib/all_moves_maia3_reversed.json");
  verifyMaiaAssets(manifest, moveBytes, reverseBytes, manifest.runtime_version, manifest.chess_library_version);
  for (const field of ["move_index_sha256", "reverse_index_sha256", "runtime_version", "chess_library_version", "inference_schema"])
    expect(() => verifyMaiaAssets({ ...manifest, [field]: "changed" }, moveBytes, reverseBytes,
      manifest.runtime_version, manifest.chess_library_version)).toThrow("provenance mismatch");
});

it("maia_reusable_module_import_performs_no_network_or_inference_and_starts_no_worker_loop", () => {
  const output = execFileSync("node", ["--input-type=module", "-e",
    "globalThis.fetch = () => { throw new Error('Unexpected network'); }; await import('./scripts/maia-inference.mjs'); console.log('imported without inference');"],
    { encoding: "utf8", timeout: 15_000 });
  expect(output.trim()).toBe("imported without inference");
});

it("maia_distribution_preserves_complete_legal_support_for_both_colors_castling_ep_and_promotions", async () => {
  for (const fixture of fixtures.cases) {
    const result = await adapter()(requestFor(fixture));
    expect(result.moves.map((move: { move_uci: string }) => move.move_uci).sort(), fixture.name).toEqual(fixture.position.legal_moves);
    expect(result.moves.reduce((sum: number, move: { probability: number }) => sum + move.probability, 0)).toBeCloseTo(1, 9);
    const board = new Chess(fixture.fen);
    result.moves.forEach((move: { move_uci: string }) => {
      board.move({ from: move.move_uci.slice(0, 2), to: move.move_uci.slice(2, 4), promotion: move.move_uci[4] });
      board.undo();
    });
  }
});

it("maia_distribution_rejects_missing_or_mismapped_legal_vocabulary_before_inference", async () => {
  const fixture = fixtures.cases.find(item => item.name === "opening-black")!;
  const firstLegalMove = "a2a3";
  for (const defect of ["missing", "reverse", "duplicate", "negative"] as const) {
    const moves: Record<string, number> = { ...forwardVocabulary };
    const reversed: Record<string, string> = { ...reverseVocabulary };
    if (defect === "missing") delete moves[firstLegalMove];
    if (defect === "reverse") reversed[String(moves[firstLegalMove])] = "0000";
    if (defect === "duplicate") moves[firstLegalMove] = moves.a2a4;
    if (defect === "negative") moves[firstLegalMove] = -1;
    const runLogits = vi.fn();
    const infer = createMaiaDistributionAdapter({ model: manifest, moveIndex: moves, reversedMoveIndex: reversed, runLogits });
    await expect(infer(requestFor(fixture))).rejects.toThrow("vocabulary");
    expect(runLogits).not.toHaveBeenCalled();
  }
});

it("maia_distribution_rejects_wrong_side_incomplete_inventory_and_noncanonical_position", async () => {
  const runLogits = vi.fn();
  const infer = adapter(runLogits);
  const request = requestFor();
  await expect(infer({ ...request, expected_side_to_move: "white" })).rejects.toThrow("opponent turn");
  await expect(infer({ ...request, position: { ...request.position, legal_moves: request.position.legal_moves.slice(1) } })).rejects.toThrow("every legal move");
  await expect(infer({ ...request, position: { ...request.position, legal_moves: [...request.position.legal_moves, request.position.legal_moves[0]] } })).rejects.toThrow("every legal move");
  await expect(infer({ ...request, position: { ...request.position, fen_key: request.position.fen_key + " 0 1" } })).rejects.toThrow();
  expect(runLogits).not.toHaveBeenCalled();
});

it("maia_distribution_rejects_delimiter_encoded_legal_moves_before_inference", async () => {
  const runLogits = vi.fn(async () => new Float32Array(vocabularySize));
  const request = requestFor();
  request.position.legal_moves = [request.position.legal_moves.slice(0, 2).join(","), ...request.position.legal_moves.slice(2)];
  await expect(adapter(runLogits)(request)).rejects.toThrow("every legal move");
  expect(runLogits).not.toHaveBeenCalled();
});

it("maia_distribution_rejects_invalid_model_outputs_without_uniform_fallback", () => {
  const prepared = prepareMaiaPosition(fixtures.cases[0].fen, forwardVocabulary, reverseVocabulary);
  for (const invalid of [NaN, Infinity, -Infinity]) {
    const logits = new Float32Array(vocabularySize);
    logits[prepared.legalEntries[0].index] = invalid;
    expect(() => decodeMaiaLogits(prepared, logits, vocabularySize)).toThrow("invalid move logits");
  }
  expect(() => decodeMaiaLogits(prepared, new Float32Array(vocabularySize - 1), vocabularySize)).toThrow();
  expect(() => decodeMaiaLogits(prepared, new Float64Array(vocabularySize), vocabularySize)).toThrow();
  expect(() => decodeMaiaLogits({ ...prepared, legalEntries: [{ index: vocabularySize, move_uci: "e8d7" }] }, new Float32Array(vocabularySize), vocabularySize)).toThrow("exceeds");
});

it("maia_distribution_preserves_explicit_softmax_zero_instead_of_unknown", async () => {
  const runLogits = vi.fn(async () => {
    const logits = new Float32Array(vocabularySize).fill(-1000);
    const prepared = prepareMaiaPosition(fixtures.cases[0].fen, forwardVocabulary, reverseVocabulary);
    logits[prepared.legalEntries[0].index] = 0;
    return logits;
  });
  const result = await adapter(runLogits)(requestFor());
  expect(result.moves.filter((move: { probability: number }) => move.probability === 0)).toHaveLength(result.moves.length - 1);
  expect(result.moves[0].probability).toBe(1);
});

it("maia_rating_pairs_are_independent_and_reject_unsupported_speed_and_rounding", async () => {
  const runLogits = vi.fn(async () => new Float32Array(vocabularySize));
  await adapter(runLogits)(requestFor());
  expect(runLogits).toHaveBeenCalledWith(expect.any(Float32Array), ratings);
  for (const value of [1550, 1000, 2000, NaN, Infinity, "1500", true]) {
    expect(() => validateMaiaRatings({ ...ratings, side_to_move_rating: value })).toThrow();
    expect(() => validateMaiaRatings({ ...ratings, other_player_rating: value })).toThrow();
  }
  expect(() => validateMaiaRatings({ ...ratings, speed: "blitz" })).toThrow();
});

it("maia_terminal_and_unavailable_model_never_fabricate_probability_evidence", async () => {
  const runLogits = vi.fn();
  const terminal = await adapter(runLogits)({ position: { fen_key: "7k/6Q1/6K1/8/8/8/8/8 b - -", legal_moves: [] },
    ratings, expected_side_to_move: "black" });
  expect(terminal).toMatchObject({ status: "terminal", evidence: null });
  expect(runLogits).not.toHaveBeenCalled();
  const unavailable = adapter(vi.fn(async () => { throw new Error("worker unavailable"); }));
  await expect(unavailable(requestFor())).rejects.toThrow("worker unavailable");
});

it("maia_request_and_model_provenance_survive_caller_mutation_during_inference", async () => {
  const request = requestFor();
  const model = structuredClone(manifest);
  const infer = createMaiaDistributionAdapter({ model, moveIndex: forwardVocabulary, reversedMoveIndex: reverseVocabulary,
    runLogits: async () => { request.ratings.side_to_move_rating = 1100; model.model_sha256 = "0".repeat(64); return new Float32Array(vocabularySize); } });
  const result = await infer(request);
  expect(result.ratings.side_to_move_rating).toBe(1500);
  expect(result.model.model_sha256).toBe(manifest.model_sha256);
});

it("maia_node_python_and_typescript_share_the_full_source_evidence_contract", async () => {
  const results = await Promise.all(fixtures.cases.map(fixture => adapter()(requestFor(fixture))));
  const checked = JSON.parse(execFileSync(resolvePython(), ["tests/fixtures/maia-probability/validate.py"], {
    input: JSON.stringify(results), encoding: "utf8", env: { ...process.env, PYTHONPATH: "backend" },
  })) as { valid: boolean; evidence: unknown; cache_key: string; checksum: string }[];
  for (const [index, item] of checked.entries()) {
    expect(item.valid, fixtures.cases[index].name).toBe(true);
    expect(openingMoveEvidenceSchema.parse(item.evidence).distribution.position).toEqual(fixtures.cases[index].position);
    expect(item.cache_key).toMatch(/^maia:v1:[0-9a-f]{64}$/);
    expect(item.checksum).toMatch(/^[0-9a-f]{64}$/);
  }
});

it("maia_local_artifact_and_independent_rating_inputs_produce_distinct_complete_outputs_offline", async () => {
  const assets = await loadMaiaAssets();
  expect(assets.model).toEqual(manifest);
  const request = requestFor(fixtures.cases.find(item => item.name === "opening-black")!);
  const baseline = await inferMaiaDistribution(request);
  for (const changedRatings of [{ ...ratings, side_to_move_rating: 1100 }, { ...ratings, other_player_rating: 1900 }]) {
    const changed = await inferMaiaDistribution({ ...request, ratings: changedRatings });
    expect(changed.ratings).toEqual(changedRatings);
    const prior = new Map(baseline.moves.map((move: { move_uci: string; probability: number }) => [move.move_uci, move.probability]));
    expect(changed.moves.some((move: { move_uci: string; probability: number }) => Math.abs(move.probability - Number(prior.get(move.move_uci))) > 1e-7)).toBe(true);
    expect(changed.moves).toHaveLength(request.position.legal_moves.length);
  }
}, 30_000);
