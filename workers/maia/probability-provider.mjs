import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { fork } from "node:child_process";
import { fileURLToPath } from "node:url";
import { validatePosition, PROBABILITY_TOLERANCE } from "./core.mjs";
import { resolveRatingInputs } from "./model.mjs";
import { MaiaError } from "./errors.mjs";

/** @typedef {import('../../app/features/product-api/contracts.ts').ProbabilityEvidence} ProbabilityEvidence */
const failure = (code) => new MaiaError(code, code);
const hash = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).sort().join(",") === [...keys].sort().join(",");
const identifier = (value) => typeof value === "string" && value.length > 0 && value.length <= 512;
const sameMoves = (left, right) => Array.isArray(left) && left.length === right.length
  && [...left].sort().every((move, index) => move === right[index]);
function freeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

/** Validate the unchanged shared position/population values plus explicit Maia context. */
export function validateProbabilityRequest(supplied) {
  if (!exactKeys(supplied, ["position", "fen", "variant", "population", "ratings", "rating_reference", "limits"])) throw failure("invalid_request");
  const request = structuredClone(supplied);
  if (request.variant !== "standard") throw failure("unsupported_variant");
  const position = validatePosition(request.fen);
  if (position.terminal) throw failure("terminal_position");
  if (!exactKeys(request.position, ["fen_key", "legal_moves"])
    || request.position.fen_key !== position.fen.split(" ").slice(0, 4).join(" ")
    || !sameMoves(request.position.legal_moves, position.legalMoves)) throw failure("invalid_position_universe");
  request.position.legal_moves = position.legalMoves;
  if (!exactKeys(request.population, ["rating_min", "rating_max", "time_controls"])
    || !Number.isSafeInteger(request.population.rating_min) || request.population.rating_min < 0
    || !Number.isSafeInteger(request.population.rating_max) || request.population.rating_max < request.population.rating_min
    || !Array.isArray(request.population.time_controls) || !request.population.time_controls.length
    || request.population.time_controls.length > 32 || !request.population.time_controls.every(identifier)
    || new Set(request.population.time_controls).size !== request.population.time_controls.length
    || !identifier(request.rating_reference)) throw failure("invalid_population_context");
  request.ratings = resolveRatingInputs({ ratings: request.ratings });
  const limits = request.limits;
  if (!exactKeys(limits, ["initialization_ms", "inference_ms", "memory_mb"])
    || !Number.isInteger(limits.initialization_ms) || limits.initialization_ms < 1 || limits.initialization_ms > 120000
    || !Number.isInteger(limits.inference_ms) || limits.inference_ms < 1 || limits.inference_ms > 30000
    || !Number.isInteger(limits.memory_mb) || limits.memory_mb < 1024 || limits.memory_mb > 2048) throw failure("invalid_resource_limits");
  return freeze(request);
}

/** A native JS heap limit cannot bound ONNX/WASM. Require an inherited hard cgroup. */
export async function hardMemoryLimitMb() {
  if (process.platform !== "linux") throw failure("memory_limit_unavailable");
  // Both controller forms cover a container-root cgroup. No setting is modified.
  const paths = ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"];
  for (const path of paths) {
    try {
      const bytes = Number((await readFile(path, "utf8")).trim());
      if (Number.isSafeInteger(bytes) && bytes > 0) return bytes / 1024 ** 2;
    } catch (error) { if (error.code !== "ENOENT" && error.code !== "EACCES") throw error; }
  }
  throw failure("memory_limit_unavailable");
}

let ownedChild = null;
/** One child across provider instances; no job queue, supervisor or durable state. */
export function createProcessExecution({ modelOptions = {} } = {}) {
  let child;
  let pending;
  let exited;
  let stderrBytes = 0;
  let stopped = false;
  function call(message) {
    return new Promise((resolve, reject) => {
      if (!child?.connected || pending) { reject(failure("worker_unavailable")); return; }
      pending = { resolve, reject };
      child.send(message, (error) => { if (error && pending) { pending.reject(failure("worker_crashed")); pending = null; } });
    });
  }
  return {
    async initialize(limits) {
      const memoryMb = await hardMemoryLimitMb();
      if (stopped) throw failure("cancelled");
      if (memoryMb < 1024 || memoryMb > limits.memory_mb) throw failure("memory_budget_unsupported");
      if (ownedChild) throw failure("worker_busy");
      child = fork(fileURLToPath(new URL("./probability-stdio.mjs", import.meta.url)), ["--model-child"], {
        stdio: ["ignore", "ignore", "pipe", "ipc"], execArgv: ["--max-old-space-size=256"],
      });
      ownedChild = child;
      exited = new Promise((resolve) => child.once("close", resolve));
      child.stderr.on("data", (data) => { stderrBytes += data.length; if (stderrBytes > 65536) child.kill("SIGKILL"); });
      child.on("message", (message) => {
        const current = pending;
        pending = null;
        if (!current) return;
        if (message?.error) current.reject(failure(message.error));
        else current.resolve(message?.value);
      });
      child.on("error", () => { pending?.reject(failure("worker_crashed")); pending = null; });
      child.once("exit", () => {
        if (ownedChild === child) ownedChild = null;
        pending?.reject(failure("worker_crashed")); pending = null;
      });
      const identity = await call({ operation: "initialize", modelOptions });
      return { identity, resources: { hard_memory_mb: memoryMb, threads: 1, concurrency: 1 } };
    },
    infer(job) { return call({ operation: "infer", job }); },
    async terminate() {
      stopped = true;
      if (!child) return;
      // Termination, including normal close, waits for actual child exit. No late output.
      child.kill("SIGKILL");
      await exited;
      child = null;
    },
  };
}

/**
 * Injectable lifecycle capability: initialize(limits), infer(job), terminate().
 * terminate MUST fence output, stop native work and resolve only after exit.
 * The default capability runs the reused model in a separate bounded process.
 */
export function createProbabilityProvider({ createExecution = createProcessExecution, now = () => new Date().toISOString() } = {}) {
  let active = null;
  let closed = false;
  async function evaluate(supplied, { signal, requestedMoves } = {}) {
    const request = validateProbabilityRequest(supplied);
    const moves = requestedMoves === undefined ? request.position.legal_moves : structuredClone(requestedMoves);
    if (!Array.isArray(moves) || !moves.length || new Set(moves).size !== moves.length
      || moves.some((move) => !request.position.legal_moves.includes(move))) throw failure("invalid_legal_moves");
    const conditional = !sameMoves(moves, request.position.legal_moves);
    let identity = null;
    let resources = null;
    const provenance = () => ({
      adapter_version: "maia-probability-v1", model_identity: identity, resources,
      input: request, requested_moves: moves, scope: conditional ? "conditional_requested_moves" : "all_legal_moves",
      limitations: ["model_not_empirical_frequency", "sample_count_unknown", "rating_reference_not_cross_platform_equivalence",
        "population_band_is_selection_not_empirical_cohort", "time_controls_not_model_inputs", "no_history_tensor",
        "upstream_checkpoint_and_export_revision_unrecorded"],
    });
    const result = (predictions, reason = null) => {
      const observedAt = predictions ? now() : null;
      if (observedAt !== null && (typeof observedAt !== "string" || !Number.isFinite(Date.parse(observedAt)))) throw failure("invalid_observation_time");
      const context = provenance();
      /** @type {ProbabilityEvidence} */
      const evidence = {
        interface_version: "0.2.0", evidence_id: `maia-${hash([context, predictions, reason, observedAt])}`,
        position: request.position, provider: "maia", model: identity?.model_version ?? null,
        basis: "population", population: request.population, personal_account: null,
        observed_at: conditional ? null : observedAt,
        distribution: predictions && !conditional ? { contract_version: 1, position: request.position,
          moves: predictions.map(({ move, probability }) => ({ move_uci: move, probability })), unknown_mass: 0 } : null,
        counts: null, unavailable_reason: conditional ? "conditional_distribution_not_coverage" : reason,
      };
      return freeze({ evidence, provenance: context,
        diagnostic: conditional ? { scope: "conditional_requested_moves", predictions, unavailable_reason: reason } : null });
    };
    if (closed) return result(null, "provider_closed");
    if (active) return result(null, "worker_busy");
    if (signal?.aborted) return result(null, "cancelled");
    const execution = createExecution();
    let stopReason = null;
    let rejectStop;
    let timer;
    let terminating;
    const stop = new Promise((_, reject) => { rejectStop = reject; });
    const terminate = () => terminating ??= Promise.resolve().then(() => execution.terminate());
    const abort = (reason) => {
      if (!stopReason) { stopReason = reason; rejectStop(failure(reason)); }
    };
    const onAbort = () => abort("cancelled");
    const lease = { abort, terminate };
    active = lease;
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
    async function bounded(operation, milliseconds, code) {
      timer = setTimeout(() => abort(code), milliseconds);
      try { return await Promise.race([Promise.resolve().then(operation), stop]); }
      finally { clearTimeout(timer); }
    }
    try {
      const initialized = await bounded(() => execution.initialize(request.limits), request.limits.initialization_ms, "initialization_timeout");
      identity = structuredClone(initialized.identity);
      resources = structuredClone(initialized.resources ?? null);
      if (!identifier(identity?.model_id) || !identifier(identity?.model_version) || !identifier(identity?.implementation_version)) throw failure("model_provenance_mismatch");
      const predictions = await bounded(() => execution.infer({ fen: request.fen, legal_moves: moves,
        model_id: identity.model_id, model_version: identity.model_version, ratings: request.ratings }), request.limits.inference_ms, "inference_timeout");
      if (!Array.isArray(predictions) || !sameMoves(predictions.map((item) => item.move), [...moves].sort())
        || predictions.some((item) => !Number.isFinite(item.probability) || item.probability < 0 || item.probability > 1)
        || Math.abs(predictions.reduce((sum, item) => sum + item.probability, 0) - 1) > PROBABILITY_TOLERANCE) throw failure("invalid_model_output");
      if (stopReason) throw failure(stopReason);
      return result(predictions.toSorted((left, right) => left.move.localeCompare(right.move)));
    } catch (error) {
      return result(null, stopReason ?? (error instanceof MaiaError ? error.code : "worker_crashed"));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      await terminate();
      if (active === lease) active = null;
    }
  }
  return {
    evidence: (request, options) => evaluate(request, options),
    diagnostic: (request, requestedMoves, options) => evaluate(request, { ...options, requestedMoves }),
    async close() { closed = true; if (active) { active.abort("provider_closed"); await active.terminate(); } },
  };
}
