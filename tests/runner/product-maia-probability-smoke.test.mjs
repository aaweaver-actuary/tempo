import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { inspectModel } from "../../workers/maia/model.mjs";
import { createProbabilityProvider, createProcessExecution } from "../../workers/maia/probability-provider.mjs";

// Explicit invocation only. Existing checked-in model parts/runtime; no build/install/download.
test("installed_real_model_one_position_legal_finite_normalized_provenance_and_exit", { timeout: 45000 }, async () => {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const modelOptions = { assetPaths: {
    modelParts: Array.from({ length: 6 }, (_, index) => `${root}public/maia3/parts/part-${String(index).padStart(2, "0")}`),
    moveIndex: `${root}app/lib/all_moves_maia3.json`, reverseIndex: `${root}app/lib/all_moves_maia3_reversed.json`,
  } };
  const identity = await inspectModel(modelOptions);
  const fixture = JSON.parse(await readFile(`${root}contracts/product-v02/fixtures/probability-unknown-mass.json`, "utf8"));
  const provider = createProbabilityProvider({ createExecution: () => createProcessExecution({ modelOptions }) });
  try {
    const result = await provider.evidence({ position: fixture.payload.position, fen: `${fixture.payload.position.fen_key} 0 1`,
      variant: "standard", population: fixture.payload.population, ratings: { elo_self: 1300, elo_oppo: 1800 },
      rating_reference: "explicit-smoke-model-scale-no-calibration-claim",
      limits: { initialization_ms: 30000, inference_ms: 10000, memory_mb: 1024 } });
    assert.equal(result.evidence.unavailable_reason, null,
      `Real inference UNVERIFIED: ${result.evidence.unavailable_reason}; assets verified as ${identity.model_version}. No passing skip.`);
    assert.equal(result.evidence.model, identity.model_version);
    assert.deepEqual(result.provenance.model_identity.checksums, identity.checksums);
    assert.deepEqual(result.provenance.input.ratings, { elo_self: 1300, elo_oppo: 1800 });
    assert.deepEqual(result.evidence.distribution.moves.map((item) => item.move_uci), fixture.payload.position.legal_moves);
    assert.ok(result.evidence.distribution.moves.every((item) => Number.isFinite(item.probability) && item.probability >= 0));
    assert.ok(Math.abs(result.evidence.distribution.moves.reduce((sum, item) => sum + item.probability, 0) - 1) < 1e-9);
    assert.equal(result.evidence.distribution.unknown_mass, 0);
    assert.equal(result.evidence.counts, null);
  } finally { await provider.close(); }
});
