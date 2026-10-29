import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { decodePredictions, preparePosition, validateRating, validateVocabularies } from "./core.mjs";
import { MaiaError } from "./errors.mjs";

const workerDirectory = dirname(fileURLToPath(import.meta.url));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
export function descriptorModelVersion(descriptor) {
  const significant = { ...descriptor };
  delete significant.model_version;
  delete significant.source_assets;
  delete significant.installed_assets;
  return `maia3-v1-${sha256(canonicalJson(significant)).slice(0, 16)}`;
}

/** Ordered model tensors; the v1 single-rating API remains unchanged. */
export function resolveRatingInputs(input, { legacy = false } = {}) {
  if (input.ratings === undefined) {
    validateRating(input.rating, { legacy });
    return { elo_self: input.rating, elo_oppo: input.rating };
  }
  if (input.rating !== undefined || !input.ratings || Object.keys(input.ratings).sort().join(",") !== "elo_oppo,elo_self") {
    throw new MaiaError("unsupported_rating", "Choose explicit ordered ratings or the legacy single rating");
  }
  validateRating(input.ratings.elo_self, { legacy });
  validateRating(input.ratings.elo_oppo, { legacy });
  return { elo_self: input.ratings.elo_self, elo_oppo: input.ratings.elo_oppo };
}

async function readAsset(path) {
  try { return await readFile(path); } catch (error) {
    throw new MaiaError(error.code === "ENOENT" ? "model_asset_missing" : "model_asset_unavailable", `Cannot read Maia asset ${path}`, { cause: error });
  }
}

async function packageVersion(packageName) {
  let directory;
  try { directory = dirname(fileURLToPath(import.meta.resolve(packageName))); } catch (error) { throw new MaiaError("model_asset_missing", `Required runtime package ${packageName} is missing`, { cause: error }); }
  while (true) {
    try {
      const manifest = JSON.parse(await readFile(join(directory, "package.json"), "utf8"));
      if (manifest.name === packageName) return manifest.version;
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    const parent = dirname(directory);
    if (parent === directory) throw new MaiaError("model_provenance_mismatch", `Cannot inspect installed runtime package ${packageName}`);
    directory = parent;
  }
}

function deepFreeze(value) {
  for (const child of Object.values(value)) if (child && typeof child === "object") deepFreeze(child);
  return Object.freeze(value);
}

async function loadVerifiedAssets({ assetsDirectory = join(workerDirectory, "assets"), assetPaths } = {}) {
  const paths = assetPaths ?? { model: join(assetsDirectory, "model.onnx"), moveIndex: join(assetsDirectory, "move-index.json"), reverseIndex: join(assetsDirectory, "reverse-index.json") };
  let descriptor;
  const descriptorBytes = await readAsset(join(workerDirectory, "model-descriptor.json"));
  try { descriptor = JSON.parse(descriptorBytes.toString("utf8")); } catch (error) { throw new MaiaError("model_provenance_mismatch", "Invalid Maia model descriptor", { cause: error }); }
  const [modelBytes, moveBytes, reverseBytes, runtimeVersion, chessVersion, coreBytes, implementationBytes, errorBytes] = await Promise.all([
    paths.modelParts ? Promise.all(paths.modelParts.map(readAsset)).then(Buffer.concat) : readAsset(paths.model),
    readAsset(paths.moveIndex), readAsset(paths.reverseIndex), packageVersion("onnxruntime-web"), packageVersion("chess.js"),
    readAsset(join(workerDirectory, "core.mjs")), readAsset(fileURLToPath(import.meta.url)), readAsset(join(workerDirectory, "errors.mjs")),
  ]);
  const checksums = { model_sha256: sha256(modelBytes), move_index_sha256: sha256(moveBytes), reverse_index_sha256: sha256(reverseBytes) };
  if (descriptor.model_sha256 !== checksums.model_sha256 || descriptor.model_bytes !== modelBytes.length) throw new MaiaError("model_hash_mismatch", "Installed Maia model checksum differs from the accepted descriptor");
  if (descriptor.move_index_sha256 !== checksums.move_index_sha256 || descriptor.reverse_index_sha256 !== checksums.reverse_index_sha256) throw new MaiaError("vocabulary_hash_mismatch", "Installed Maia vocabulary checksum differs from the accepted descriptor");
  if (descriptor.descriptor_version !== 1 || descriptor.model_id !== "maia3-onnx" || descriptor.model_version !== descriptorModelVersion(descriptor) || descriptor.runtime_name !== "onnxruntime-web/wasm" || descriptor.runtime_version !== runtimeVersion || descriptor.chess_library_name !== "chess.js" || descriptor.chess_library_version !== chessVersion || descriptor.inference_schema !== "maia-requested-legal-softmax-v1" || descriptor.runtime_threads !== 1) throw new MaiaError("model_provenance_mismatch", "Installed Maia runtime, model identity or inference schema differs from its descriptor");
  let moveIndex;
  let reverseIndex;
  try { moveIndex = JSON.parse(moveBytes.toString("utf8")); reverseIndex = JSON.parse(reverseBytes.toString("utf8")); } catch (error) { throw new MaiaError("model_provenance_mismatch", "Invalid Maia move vocabulary JSON", { cause: error }); }
  validateVocabularies(moveIndex, reverseIndex);
  const implementationFiles = { "core.mjs": sha256(coreBytes), "model.mjs": sha256(implementationBytes), "errors.mjs": sha256(errorBytes), "model-descriptor.json": sha256(descriptorBytes) };
  const runtimeContext = { node: process.version, platform: process.platform, architecture: process.arch, runtime_version: runtimeVersion, chess_library_version: chessVersion };
  const implementationHash = sha256(canonicalJson({ files: implementationFiles, runtime: runtimeContext }));
  const identity = deepFreeze({ descriptor, model_id: descriptor.model_id, model_version: descriptor.model_version, implementation_version: `maia-worker-v1-${implementationHash.slice(0, 16)}`, runtime_name: descriptor.runtime_name, runtime_version: runtimeVersion, chess_library_version: chessVersion, checksums, implementation_sha256: implementationHash, implementation_files: implementationFiles, runtime_context: runtimeContext });
  return { identity, modelBytes, moveIndex, reverseIndex };
}

/** Verify installed bytes and versions without initializing an inference session. */
export async function inspectModel(options = {}) {
  const { identity } = await loadVerifiedAssets(options);
  return identity;
}

function verifySessionMetadata(session) {
  const expectedInputs = { tokens: [64, 12], elo_self: [], elo_oppo: [] };
  const expectedOutputs = { logits_move: [4352], logits_value: [3] };
  for (const [metadata, expected] of [[session.inputMetadata, expectedInputs], [session.outputMetadata, expectedOutputs]]) {
    if (!Array.isArray(metadata) || metadata.length !== Object.keys(expected).length || new Set(metadata.map((item) => item.name)).size !== metadata.length) throw new MaiaError("model_provenance_mismatch", "Unexpected ONNX tensor metadata");
    for (const item of metadata) {
      const trailingShape = expected[item.name];
      if (!trailingShape || !item.isTensor || item.type !== "float32" || item.shape.length !== trailingShape.length + 1 || item.shape.slice(1).some((dimension, index) => dimension !== trailingShape[index])) throw new MaiaError("model_provenance_mismatch", `Unsupported ONNX tensor ${item.name}`);
    }
  }
}

/** Create one prewarmed, serial, single-thread WASM model session. */
export async function createModel(options = {}) {
  const legacyRatings = options.allowLegacyRatings === true;
  const { identity, modelBytes, moveIndex, reverseIndex } = await loadVerifiedAssets(options);
  let ort;
  let session;
  try {
    ort = await import("onnxruntime-web/wasm");
    ort.env.wasm.numThreads = 1;
    ort.env.wasm.proxy = false;
    if (ort.env.versions.web !== identity.runtime_version) throw new MaiaError("model_provenance_mismatch", "Loaded ONNX runtime differs from installed runtime");
    session = await ort.InferenceSession.create(modelBytes, { executionProviders: ["wasm"] });
    verifySessionMetadata(session);
  } catch (error) {
    if (session) await session.release().catch(() => {});
    if (error instanceof MaiaError) throw error;
    throw new MaiaError("model_loading_failed", "Cannot initialize bundled Maia model", { retryable: true, cause: error });
  }
  let disposed = false;
  let running = false;
  return {
    identity,
    async infer(job, { onInferenceStart } = {}) {
      if (disposed) throw new MaiaError("model_unavailable", "Maia model session has been disposed", { retryable: true });
      if (running) throw new MaiaError("worker_busy", "Only one Maia inference may execute at a time", { retryable: true });
      const input = structuredClone(job);
      if (input.model_id !== identity.model_id || input.model_version !== identity.model_version) throw new MaiaError("unsupported_model", "Requested model identity is not installed");
      const ratings = resolveRatingInputs(input, { legacy: legacyRatings });
      const prepared = preparePosition(input.fen, input.legal_moves, moveIndex, reverseIndex);
      running = true;
      try {
        const feeds = {
          tokens: new ort.Tensor("float32", prepared.tokens, [1, 64, 12]),
          elo_self: new ort.Tensor("float32", Float32Array.of(ratings.elo_self), [1]),
          elo_oppo: new ort.Tensor("float32", Float32Array.of(ratings.elo_oppo), [1]),
        };
        onInferenceStart?.();
        const output = await session.run(feeds);
        const logits = output.logits_move;
        const value = output.logits_value;
        if (logits?.type !== "float32" || JSON.stringify(logits.dims) !== "[1,4352]" || value?.type !== "float32" || JSON.stringify(value.dims) !== "[1,3]" || !(value.data instanceof Float32Array) || !value.data.every(Number.isFinite)) throw new MaiaError("invalid_model_output", "Unexpected Maia output tensor type, shape or value logits");
        return decodePredictions(logits.data, prepared);
      } catch (error) {
        if (error instanceof MaiaError) throw error;
        throw new MaiaError("model_execution_failed", "Maia inference failed", { retryable: true, cause: error });
      } finally { running = false; }
    },
    async dispose() {
      if (disposed) return;
      disposed = true;
      await session.release();
    },
  };
}
