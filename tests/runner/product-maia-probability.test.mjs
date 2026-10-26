import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { Readable, Writable } from "node:stream";
import { fileURLToPath } from "node:url";
import { createProbabilityProvider, validateProbabilityRequest } from "../../workers/maia/probability-provider.mjs";
import { probabilityStdio } from "../../workers/maia/probability-stdio.mjs";
import { resolveRatingInputs } from "../../workers/maia/model.mjs";
import { MaiaError } from "../../workers/maia/errors.mjs";

const fixture = JSON.parse(await readFile(new URL("../../contracts/product-v02/fixtures/probability-unknown-mass.json", import.meta.url), "utf8"));
export function request() {
  return { position: structuredClone(fixture.payload.position), fen: `${fixture.payload.position.fen_key} 0 1`,
    variant: "standard", population: structuredClone(fixture.payload.population),
    ratings: { elo_self: 1300, elo_oppo: 1800 }, rating_reference: "explicit-operator-model-scale",
    limits: { initialization_ms: 100, inference_ms: 100, memory_mb: 1024 } };
}
const identity = { model_id: "fake-model", model_version: "fake-version", implementation_version: "fake-implementation",
  checksums: { model_sha256: "synthetic" }, descriptor: { upstream_checkpoint: null } };
function fakeExecution(overrides = {}) {
  const state = { jobs: [], initialized: 0, terminated: 0, running: false };
  const execution = {
    async initialize(limits) { state.initialized++; state.limits = limits; return { identity }; },
    async infer(job) { state.jobs.push(job); return job.legal_moves.map((move) => ({ move, probability: 1 / job.legal_moves.length })); },
    async terminate() { state.terminated++; state.running = false; },
    ...overrides,
  };
  return { state, execution, provider: createProbabilityProvider({ createExecution: () => execution, now: () => "2026-10-26T12:00:00Z" }) };
}
const nextTurn = () => new Promise((resolve) => setImmediate(resolve));

test("all_legal_moves_requested", async () => {
  const { provider, state } = fakeExecution();
  const input = request();
  const result = await provider.evidence(input);
  assert.deepEqual(state.jobs[0].legal_moves, input.position.legal_moves);
  assert.equal(state.jobs[0].legal_moves.length, 20);
  assert.equal(result.provenance.scope, "all_legal_moves");
  assert.equal(state.terminated, 1);
});

test("legal_distribution_has_expected_scope", async () => {
  const { provider } = fakeExecution();
  const result = await provider.evidence(request());
  assert.equal(result.evidence.unavailable_reason, null);
  assert.equal(result.evidence.distribution.unknown_mass, 0);
  assert.equal(result.evidence.distribution.moves.length, 20);
  assert.ok(Math.abs(result.evidence.distribution.moves.reduce((sum, item) => sum + item.probability, 0) - 1) < 1e-9);
  assert.deepEqual(result.evidence.distribution.position, result.evidence.position);
  assert.ok(Object.isFrozen(result.evidence.distribution.moves));
});

test("subset_result_is_marked_conditional", async () => {
  const { provider, state } = fakeExecution();
  const result = await provider.diagnostic(request(), ["e2e4", "d2d4"]);
  assert.deepEqual(state.jobs[0].legal_moves, ["e2e4", "d2d4"]);
  assert.equal(result.provenance.scope, "conditional_requested_moves");
  assert.equal(result.evidence.distribution, null);
  assert.equal(result.evidence.unavailable_reason, "conditional_distribution_not_coverage");
  assert.deepEqual(result.diagnostic.predictions.map((item) => item.probability), [0.5, 0.5]);
  assert.equal(result.evidence.observed_at, null);
});

test("sample_count_is_unknown_not_zero", async () => {
  const { provider } = fakeExecution();
  const result = await provider.evidence(request());
  assert.equal(result.evidence.counts, null);
  assert.ok(result.provenance.limitations.includes("sample_count_unknown"));
  assert.equal(result.evidence.basis, "population");
  assert.equal(result.evidence.personal_account, null);
});

test("all_rating_inputs_preserved", async () => {
  const { provider, state } = fakeExecution();
  const input = request();
  const result = await provider.evidence(input);
  assert.deepEqual(state.jobs[0].ratings, { elo_self: 1300, elo_oppo: 1800 });
  assert.deepEqual(resolveRatingInputs(state.jobs[0]), input.ratings);
  assert.deepEqual(result.provenance.input.ratings, input.ratings);
  assert.equal(result.provenance.input.rating_reference, input.rating_reference);
  assert.deepEqual(result.evidence.population, input.population);
  assert.ok(result.provenance.limitations.includes("time_controls_not_model_inputs"));
});

test("legacy_single_rating_inputs_preserved_without_rounding", () => {
  assert.deepEqual(resolveRatingInputs({ rating: 1500 }), { elo_self: 1500, elo_oppo: 1500 });
  assert.deepEqual(resolveRatingInputs({ rating: 1575 }, { legacy: true }), { elo_self: 1575, elo_oppo: 1575 });
  for (const input of [{ rating: 1575 }, { rating: 1500, ratings: { elo_self: 1300, elo_oppo: 1800 } },
    { ratings: { elo_self: 1300 } }, { ratings: { elo_self: 1300, elo_oppo: 1810 } }]) {
    assert.throws(() => resolveRatingInputs(input), (error) => error.code === "unsupported_rating");
  }
});

test("model_version_and_position_provenance", async () => {
  const { provider } = fakeExecution();
  const input = request();
  input.fen = `${input.position.fen_key} 12 87`;
  const result = await provider.evidence(input);
  assert.equal(result.evidence.model, identity.model_version);
  assert.equal(result.evidence.observed_at, "2026-10-26T12:00:00Z");
  assert.deepEqual(result.provenance.model_identity, identity);
  assert.equal(result.provenance.input.fen, input.fen);
  assert.equal(result.provenance.input.variant, "standard");
  assert.deepEqual(result.provenance.input.limits, input.limits);
  const other = request(); other.ratings.elo_oppo = 1700;
  assert.notEqual((await provider.evidence(other)).evidence.evidence_id, result.evidence.evidence_id);
});

test("unsupported_or_malformed_input_rejected", async () => {
  const { provider, state } = fakeExecution();
  const mutations = [
    (r) => { r.variant = "chess960"; }, (r) => { r.fen = "bad"; },
    (r) => { r.position.legal_moves.pop(); }, (r) => { r.position.legal_moves[1] = r.position.legal_moves[0]; },
    (r) => { r.position.fen_key = r.position.fen_key.replace(" w ", " b "); },
    (r) => { delete r.population; }, (r) => { r.population.time_controls = []; },
    (r) => { r.population.rating_min = 2001; }, (r) => { r.ratings.elo_oppo = 1850; },
    (r) => { r.rating_reference = ""; }, (r) => { r.limits.inference_ms = Infinity; },
    (r) => { r.limits.memory_mb = 0; }, (r) => { r.limits.initialization_ms = 0; },
    (r) => { r.requested_moves = ["e2e4"]; },
  ];
  for (const mutate of mutations) { const input = request(); mutate(input); await assert.rejects(provider.evidence(input)); }
  assert.equal(state.initialized, 0);
});

test("timeout_cancel_and_load_failure_do_not_leak_work", async (t) => {
  for (const stage of ["initialize", "infer"]) await t.test(`${stage}_timeout`, async () => {
    const { provider, state } = fakeExecution({ [stage]: () => new Promise(() => {}) });
    const input = request(); input.limits.initialization_ms = 10; input.limits.inference_ms = 10;
    const result = await provider.evidence(input);
    assert.equal(result.evidence.unavailable_reason, stage === "initialize" ? "initialization_timeout" : "inference_timeout");
    assert.equal(state.terminated, 1);
    assert.equal(result.evidence.distribution, null);
  });
  for (const stage of ["initialize", "infer"]) await t.test(`${stage}_cancel`, async () => {
    let started;
    const ready = new Promise((resolve) => { started = resolve; });
    const { provider, state } = fakeExecution({ [stage]: () => { started(); return new Promise(() => {}); } });
    const controller = new AbortController();
    const pending = provider.evidence(request(), { signal: controller.signal });
    await ready; controller.abort();
    assert.equal((await pending).evidence.unavailable_reason, "cancelled");
    assert.equal(state.terminated, 1);
  });
  await t.test("missing_assets", async () => {
    const { provider, state } = fakeExecution({ initialize: async () => { throw new MaiaError("model_asset_missing", "missing"); } });
    assert.equal((await provider.evidence(request())).evidence.unavailable_reason, "model_asset_missing");
    assert.equal(state.terminated, 1);
  });
});

test("termination_is_awaited_before_capacity_release_and_late_output_is_fenced", async () => {
  let exit; let finish;
  const { provider } = fakeExecution({
    infer: () => new Promise((resolve) => { finish = resolve; }),
    terminate: () => new Promise((resolve) => { exit = resolve; }),
  });
  const controller = new AbortController();
  const pending = provider.evidence(request(), { signal: controller.signal });
  await nextTurn(); controller.abort(); await nextTurn();
  assert.equal((await provider.evidence(request())).evidence.unavailable_reason, "worker_busy");
  finish(request().position.legal_moves.map((move) => ({ move, probability: 0.05 })));
  exit();
  assert.equal((await pending).evidence.unavailable_reason, "cancelled");
  await provider.close();
  assert.equal((await provider.evidence(request())).evidence.unavailable_reason, "provider_closed");
});

test("concurrency_and_shutdown_are_bounded", async () => {
  let entered;
  const ready = new Promise((resolve) => { entered = resolve; });
  const { provider, state } = fakeExecution({ infer: () => { entered(); return new Promise(() => {}); } });
  const pending = provider.evidence(request());
  await ready;
  assert.equal((await provider.evidence(request())).evidence.unavailable_reason, "worker_busy");
  await provider.close();
  assert.equal((await pending).evidence.unavailable_reason, "provider_closed");
  assert.equal(state.terminated, 1);
});

test("pre_cancel_and_execution_creation_failure_start_no_work", async () => {
  const { provider, state } = fakeExecution();
  const controller = new AbortController(); controller.abort();
  assert.equal((await provider.evidence(request(), { signal: controller.signal })).evidence.unavailable_reason, "cancelled");
  assert.equal(state.initialized, 0);
  const unavailable = createProbabilityProvider({ createExecution: () => { throw new MaiaError("worker_crashed", "spawn failed"); } });
  assert.equal((await unavailable.evidence(request())).evidence.unavailable_reason, "worker_crashed");
  await unavailable.close();
});

for (const code of ["worker_crashed", "model_loading_failed", "memory_limit_unavailable"]) test(`unavailable_maia_preserves_explorer_fallback:${code}`, async () => {
  const explorer = Object.freeze(structuredClone(fixture.payload));
  const { provider } = fakeExecution({ infer: async () => { throw new MaiaError(code, code); } });
  const result = await provider.evidence(request());
  const chosen = result.evidence.distribution ? result.evidence : explorer;
  assert.equal(chosen, explorer);
  assert.equal(result.evidence.unavailable_reason, code);
  assert.deepEqual(chosen, fixture.payload);
});

test("invalid_output_never_becomes_coverage", async () => {
  const invalid = [[], [{ move: "e2e4", probability: 1 }],
    request().position.legal_moves.map((move) => ({ move, probability: NaN })),
    request().position.legal_moves.map((move) => ({ move, probability: 0.1 }))];
  for (const output of invalid) {
    const { provider, state } = fakeExecution({ infer: async () => output });
    const result = await provider.evidence(request());
    assert.equal(result.evidence.unavailable_reason, "invalid_model_output");
    assert.equal(state.terminated, 1);
  }
});

test("stdio_one_request_returns_shared_evidence_and_closes", async () => {
  const { provider, state } = fakeExecution();
  let text = "";
  const output = new Writable({ write(chunk, _encoding, done) { text += chunk; done(); } });
  await probabilityStdio({ input: Readable.from([JSON.stringify(request())]), output, provider });
  assert.equal(JSON.parse(text).evidence.interface_version, "0.2.0");
  assert.equal(state.terminated, 1);
  assert.equal((await provider.evidence(request())).evidence.unavailable_reason, "provider_closed");
});

test("no_database_or_study_import", async () => {
  for (const file of ["probability-provider.mjs", "probability-stdio.mjs", "model.mjs", "core.mjs"]) {
    const source = (await readFile(new URL(`../../workers/maia/${file}`, import.meta.url), "utf8")).replace(/\/\*[\s\S]*?\*\//g, "");
    const imports = [...source.matchAll(/(?:from\s*|import\s*\()\s*["']([^"']+)["']/g)].map((match) => match[1]);
    assert.ok(imports.every((path) => path.startsWith("node:") || path.startsWith("./") || ["chess.js", "onnxruntime-web/wasm"].includes(path)), imports.join(","));
  }
});

test("evidence_is_validated_by_published_python_contract_without_dto_copy", async () => {
  const python = process.env.TEMPO_PYTHON;
  assert.ok(python, "Set TEMPO_PYTHON to an already-installed Python with published contract dependencies; no passing skip");
  const { provider } = fakeExecution();
  const available = await provider.evidence(request());
  const conditional = await provider.diagnostic(request(), ["e2e4"]);
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const result = spawnSync(python, ["-c", "import json,sys; from app.contracts.product_v02.analysis import ProbabilityEvidence; values=json.load(sys.stdin); [ProbabilityEvidence.model_validate_json(json.dumps(v)) for v in values]; print('2 published ProbabilityEvidence values valid')"], {
    input: JSON.stringify([available.evidence, conditional.evidence]), encoding: "utf8", timeout: 10000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", PYTHONPATH: ["backend", "packages/evidence-math/src", "contracts/analysis-jobs/src"].map((path) => `${root}${path}`).join(":") },
  });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  assert.match(result.stdout, /2 published ProbabilityEvidence values valid/);
});
