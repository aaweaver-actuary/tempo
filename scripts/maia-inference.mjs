// Reusable local Maia inference. Importing this module never starts the worker loop.
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Chess } from "chess.js";
import * as ort from "onnxruntime-web/wasm";

const supportedRatings = new Set(Array.from({ length: 9 }, (_, index) => 1100 + index * 100));
const pieceTypes = ["P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"];
const probabilityTolerance = 1e-9;
ort.env.wasm.numThreads = 1;

export function validateMaiaRatings(ratings) {
  if (!ratings || Object.keys(ratings).sort().join(",") !== "other_player_rating,side_to_move_rating" ||
      !supportedRatings.has(ratings.side_to_move_rating) || !supportedRatings.has(ratings.other_player_rating))
    throw new Error("Unsupported Maia rating context; expected two explicit supported levels and no speed input");
}

function mirrorSquare(square) { return `${square[0]}${9 - Number(square[1])}`; }
function mirrorMove(move) { return `${mirrorSquare(move.slice(0, 2))}${mirrorSquare(move.slice(2, 4))}${move.slice(4)}`; }
function mirrorFen(fen) {
  const [placement, active, castling, ep, halfmove, fullmove] = fen.split(" ");
  const swap = (rank) => [...rank].map((piece) =>
    /[a-z]/.test(piece) ? piece.toUpperCase() : /[A-Z]/.test(piece) ? piece.toLowerCase() : piece).join("");
  const rights = castling === "-" ? "-" : [
    castling.includes("k") ? "K" : "", castling.includes("q") ? "Q" : "",
    castling.includes("K") ? "k" : "", castling.includes("Q") ? "q" : "",
  ].join("") || "-";
  return `${placement.split("/").reverse().map(swap).join("/")} ${active === "w" ? "b" : "w"} ${rights} ${ep === "-" ? "-" : mirrorSquare(ep)} ${halfmove} ${fullmove}`;
}

export function prepareMaiaPosition(fen, moveIndex, reversedMoveIndex) {
  const originalBoard = new Chess(fen);
  const sideToMove = originalBoard.turn() === "w" ? "white" : "black";
  const normalizedFen = sideToMove === "black" ? mirrorFen(originalBoard.fen()) : originalBoard.fen();
  const board = new Chess(normalizedFen);
  const originalLegalMoves = originalBoard.moves({ verbose: true }).map((move) =>
    `${move.from}${move.to}${move.promotion ?? ""}`).sort();
  const legalEntries = board.moves({ verbose: true }).map((move) => {
    const modelMove = `${move.from}${move.to}${move.promotion ?? ""}`;
    const index = moveIndex[modelMove];
    if (!Number.isInteger(index) || index < 0 || reversedMoveIndex[String(index)] !== modelMove)
      throw new Error(`Maia vocabulary omits or mismaps legal move ${modelMove}`);
    return { index, move_uci: sideToMove === "black" ? mirrorMove(modelMove) : modelMove };
  });
  if (legalEntries.map((entry) => entry.move_uci).sort().join(",") !== originalLegalMoves.join(","))
    throw new Error("Maia orientation does not preserve the complete legal move universe");
  const tokens = new Float32Array(64 * 12);
  normalizedFen.split(" ")[0].split("/").forEach((rankText, rankFromTop) => {
    let file = 0;
    for (const piece of rankText) {
      if (/\d/.test(piece)) file += Number(piece);
      else { tokens[((7 - rankFromTop) * 8 + file) * 12 + pieceTypes.indexOf(piece)] = 1; file += 1; }
    }
  });
  return { tokens, legalEntries, originalLegalMoves, sideToMove };
}

export function decodeMaiaLogits(prepared, logits, vocabularySize) {
  if (!(logits instanceof Float32Array) || logits.length !== vocabularySize ||
      !logits.every(Number.isFinite)) throw new Error("Maia model returned invalid move logits");
  if (!prepared.legalEntries.length) return [];
  if (prepared.legalEntries.some((entry) => entry.index >= logits.length))
    throw new Error("Maia vocabulary index exceeds model output");
  const maximum = Math.max(...prepared.legalEntries.map((entry) => logits[entry.index]));
  const weights = prepared.legalEntries.map((entry) => Math.exp(logits[entry.index] - maximum));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  if (!Number.isFinite(total) || total <= 0) throw new Error("Maia softmax has invalid total weight");
  const moves = prepared.legalEntries.map((entry, index) => ({ move_uci: entry.move_uci, probability: weights[index] / total }));
  if (moves.some((move) => !Number.isFinite(move.probability) || move.probability < 0) ||
      Math.abs(moves.reduce((sum, move) => sum + move.probability, 0) - 1) > probabilityTolerance)
    throw new Error("Maia probabilities have invalid normalization");
  return moves.sort((left, right) => right.probability - left.probability);
}

export function createMaiaDistributionAdapter({ model, moveIndex, reversedMoveIndex, runLogits }) {
  // Snapshot injected inputs so a caller cannot mutate provenance during inference.
  const descriptor = structuredClone(model);
  const forwardVocabulary = { ...moveIndex };
  const reverseVocabulary = { ...reversedMoveIndex };
  return async function infer(request) {
    const input = structuredClone(request);
    validateMaiaRatings(input.ratings);
    const prepared = prepareMaiaPosition(`${input.position.fen_key} 0 1`, forwardVocabulary, reverseVocabulary);
    if (new Chess(`${input.position.fen_key} 0 1`).fen().split(" ").slice(0, 4).join(" ") !== input.position.fen_key)
      throw new Error("Maia request requires a canonical four-field position key");
    if (input.expected_side_to_move !== prepared.sideToMove)
      throw new Error("Maia request does not select the expected opponent turn");
    if (input.position.legal_moves.slice().sort().join(",") !== prepared.originalLegalMoves.join(","))
      throw new Error("Maia request must contain every legal move exactly once");
    if (!prepared.legalEntries.length) return { status: "terminal", position: input.position, evidence: null };
    const logits = await runLogits(prepared.tokens, input.ratings);
    return { contract_version: 1, position: input.position, side_to_move: prepared.sideToMove,
      ratings: input.ratings, model: structuredClone(descriptor),
      moves: decodeMaiaLogits(prepared, logits, Object.keys(forwardVocabulary).length) };
  };
}

let assetsPromise;
let sessionPromise;
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

export function verifyMaiaAssets(model, moveBytes, reverseBytes, runtimeVersion, chessVersion) {
  if (sha256(moveBytes) !== model.move_index_sha256 || sha256(reverseBytes) !== model.reverse_index_sha256 ||
      runtimeVersion !== model.runtime_version || chessVersion !== model.chess_library_version ||
      model.inference_schema !== "maia-legal-softmax-v1") throw new Error("Maia artifact/runtime provenance mismatch");
}

export async function loadMaiaAssets() {
  assetsPromise ??= (async () => {
    const [modelText, moveBytes, reverseBytes, chessPackage] = await Promise.all([
      readFile(resolve(import.meta.dirname, "../backend/app/maia_probability_manifest.json"), "utf8"),
      readFile(resolve(import.meta.dirname, "../app/lib/all_moves_maia3.json")),
      readFile(resolve(import.meta.dirname, "../app/lib/all_moves_maia3_reversed.json")),
      readFile(resolve(dirname(fileURLToPath(import.meta.resolve("chess.js"))), "../../package.json"), "utf8"),
    ]);
    const model = JSON.parse(modelText);
    verifyMaiaAssets(model, moveBytes, reverseBytes, ort.env.versions.web, JSON.parse(chessPackage).version);
    return { model, moveIndex: JSON.parse(moveBytes.toString()), reversedMoveIndex: JSON.parse(reverseBytes.toString()) };
  })().catch((error) => { assetsPromise = undefined; throw error; });
  return structuredClone(await assetsPromise);
}

async function loadSession() {
  sessionPromise ??= (async () => {
    const { model } = await loadMaiaAssets();
    const parts = await Promise.all(Array.from({ length: 6 }, (_, index) =>
      readFile(resolve(import.meta.dirname, `../public/maia3/parts/part-${String(index).padStart(2, "0")}`))));
    const bytes = Buffer.concat(parts);
    if (sha256(bytes) !== model.model_sha256) throw new Error("Maia model checksum does not match the accepted artifact");
    return ort.InferenceSession.create(bytes, { executionProviders: ["wasm"] });
  })().catch((error) => { sessionPromise = undefined; throw error; });
  return sessionPromise;
}

async function runLocalLogits(tokens, ratings) {
  const session = await loadSession();
  const result = await session.run({
    tokens: new ort.Tensor("float32", tokens, [1, 64, 12]),
    elo_self: new ort.Tensor("float32", Float32Array.of(ratings.side_to_move_rating), [1]),
    elo_oppo: new ort.Tensor("float32", Float32Array.of(ratings.other_player_rating), [1]),
  });
  return result.logits_move?.data;
}

// Explicit diagnostic initialization; ordinary imports remain lazy.
export async function initializeMaiaInference() {
  await loadSession();
}

export async function inferMaiaDistribution(request) {
  const assets = await loadMaiaAssets();
  return createMaiaDistributionAdapter({ ...assets, runLogits: runLocalLogits })(request);
}

// Compatibility entry point used by the existing coverage worker.
export async function analyzeMaiaPosition(fen, elo) {
  const { moveIndex, reversedMoveIndex } = await loadMaiaAssets();
  const prepared = prepareMaiaPosition(fen, moveIndex, reversedMoveIndex);
  if (!prepared.legalEntries.length) return [];
  return decodeMaiaLogits(prepared, await runLocalLogits(prepared.tokens,
    { side_to_move_rating: elo, other_player_rating: elo }), Object.keys(moveIndex).length);
}
