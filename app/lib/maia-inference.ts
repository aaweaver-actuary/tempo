import { Chess, type Move } from "chess.js";
import * as ort from "onnxruntime-web/wasm";
import { decodePredictions, preparePosition } from "../../workers/maia/core.mjs";
import moveIndex from "./all_moves_maia3.json";
import reversedMoveIndex from "./all_moves_maia3_reversed.json";
import type { EngineMove } from "./analysis-engines";
import type { FenString } from "../types";
import { asSanMove, asUciMove } from "../types";

let assetRoot = "";

function assetUrl(path: string) {
  return new URL(path, assetRoot).href;
}
let maiaReady: Promise<ort.InferenceSession> | undefined;
const MAIA_INITIALIZATION_TIMEOUT_MS = 45_000;

function withTimeout<T>(
  promise: Promise<T>,
  milliseconds: number,
  message: string,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(
      () => reject(new Error(message)),
      milliseconds,
    );
    promise.then(
      (value) => {
        globalThis.clearTimeout(timeout);
        resolve(value);
      },
      (error) => {
        globalThis.clearTimeout(timeout);
        reject(error);
      },
    );
  });
}

function loadMaia(onProgress?: (progress: number) => void) {
  if (!maiaReady)
    maiaReady = (async () => {
      ort.env.wasm.wasmPaths = {
        mjs: assetUrl("ort/ort-wasm-simd-threaded.mjs"),
        wasm: assetUrl("ort/ort-wasm-simd-threaded.wasm"),
      };
      ort.env.wasm.numThreads = crossOriginIsolated
        ? Math.min(2, navigator.hardwareConcurrency || 1)
        : 1;
      onProgress?.(5);
      const chunks: Uint8Array[] = [];
      for (let index = 0; index < 6; index++) {
        const response = await fetch(
          assetUrl(`maia3/parts/part-${String(index).padStart(2, "0")}`),
        );
        if (!response.ok) throw new Error("Could not load Maia model");
        chunks.push(new Uint8Array(await response.arrayBuffer()));
        onProgress?.(Math.round(((index + 1) / 6) * 90));
      }
      const data = new Uint8Array(
        chunks.reduce((sum, chunk) => sum + chunk.length, 0),
      );
      let offset = 0;
      for (const chunk of chunks) {
        data.set(chunk, offset);
        offset += chunk.length;
      }
      const session = await withTimeout(
        ort.InferenceSession.create(data, { executionProviders: ["wasm"] }),
        MAIA_INITIALIZATION_TIMEOUT_MS,
        "Maia initialization timed out. Check that the ONNX Runtime and model versions match, then retry.",
      );
      onProgress?.(100);
      return session;
    })().catch((error) => {
      maiaReady = undefined;
      throw error;
    });
  return maiaReady;
}

export async function runMaiaInference(
  fen: FenString,
  elo: number,
  root: string,
  onProgress?: (progress: number) => void,
): Promise<EngineMove[]> {
  assetRoot = root;
  const session = await loadMaia(onProgress);
  const position = new Chess(fen);
  const requestedMoves = position.moves({ verbose: true }).map((move) =>
    `${move.from}${move.to}${move.promotion ?? ""}`);
  // Terminal legacy callers retain their established empty-list response.
  if (!requestedMoves.length) return [];
  const prepared = preparePosition(fen, requestedMoves, moveIndex, reversedMoveIndex);
  const result = await session.run({
    tokens: new ort.Tensor("float32", prepared.tokens, [1, 64, 12]),
    elo_self: new ort.Tensor("float32", Float32Array.of(elo), [1]),
    elo_oppo: new ort.Tensor("float32", Float32Array.of(elo), [1]),
  });
  const logits = result.logits_move;
  if (logits?.type !== "float32" || logits.dims.join(",") !== "1,4352")
    throw new Error("Maia model/runtime output mismatch: expected float32 move logits [1,4352]");
  const moves: EngineMove[] = [];
  for (const prediction of decodePredictions(logits.data, prepared)) {
    const uci = asUciMove(prediction.move);
    const move: Move = position.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] });
    position.undo();
    moves.push({ uci, san: asSanMove(move.san), probability: prediction.probability });
  }
  return moves.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0));
}
