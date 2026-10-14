import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { Chess } from "chess.js";
import * as ort from "onnxruntime-web/wasm";

const assetDirectory = resolve(import.meta.dirname, "../public/maia3/parts");
const moveIndex = JSON.parse(await readFile(resolve(import.meta.dirname, "../app/lib/all_moves_maia3.json"), "utf8"));
const reversedMoveIndex = JSON.parse(await readFile(resolve(import.meta.dirname, "../app/lib/all_moves_maia3_reversed.json"), "utf8"));
const pieceTypes = ["P", "N", "B", "R", "Q", "K", "p", "n", "b", "r", "q", "k"];
ort.env.wasm.numThreads = 1;

let sessionPromise;
async function loadSession() {
  sessionPromise ??= (async () => {
    const parts = await Promise.all(Array.from({ length: 6 }, (_, index) =>
      readFile(resolve(assetDirectory, `part-${String(index).padStart(2, "0")}`))));
    return ort.InferenceSession.create(Buffer.concat(parts), { executionProviders: ["wasm"] });
  })().catch((error) => { sessionPromise = undefined; throw error; });
  return sessionPromise;
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

export async function analyzeMaiaPosition(fen, elo) {
  const session = await loadSession();
  const isBlack = fen.split(" ")[1] === "b";
  const normalizedFen = isBlack ? mirrorFen(fen) : fen;
  const board = new Chess(normalizedFen);
  const tokens = new Float32Array(64 * 12);
  normalizedFen.split(" ")[0].split("/").forEach((rankText, rankFromTop) => {
    let file = 0;
    for (const piece of rankText) {
      if (/\d/.test(piece)) file += Number(piece);
      else { tokens[((7 - rankFromTop) * 8 + file) * 12 + pieceTypes.indexOf(piece)] = 1; file += 1; }
    }
  });
  const legalIndices = [];
  for (const move of board.moves({ verbose: true })) {
    const moveUci = `${move.from}${move.to}${move.promotion ?? ""}`;
    if (moveIndex[moveUci] === undefined) throw new Error(`Missing legal Maia move: ${moveUci}`);
    legalIndices.push(moveIndex[moveUci]);
  }
  if (!legalIndices.length) return [];
  const result = await session.run({
    tokens: new ort.Tensor("float32", tokens, [1, 64, 12]),
    elo_self: new ort.Tensor("float32", Float32Array.of(elo), [1]),
    elo_oppo: new ort.Tensor("float32", Float32Array.of(elo), [1]),
  });
  const logits = result.logits_move?.data;
  if (!(logits instanceof Float32Array)) throw new Error("Maia model returned invalid move logits");
  if (legalIndices.some((index) => !Number.isFinite(logits[index]))) throw new Error("Nonfinite Maia logits");
  const maximum = Math.max(...legalIndices.map((index) => logits[index]));
  const weights = legalIndices.map((index) => Math.exp(logits[index] - maximum));
  const total = weights.reduce((sum, weight) => sum + weight, 0);
  const originalBoard = new Chess(fen);
  const moves = [];
  for (const [itemIndex, index] of legalIndices.entries()) {
    const rawMove = reversedMoveIndex[String(index)];
    const moveUci = isBlack ? mirrorMove(rawMove) : rawMove;
    try {
      const move = originalBoard.move({ from: moveUci.slice(0, 2), to: moveUci.slice(2, 4), promotion: moveUci[4] || "q" });
      originalBoard.undo();
      if (move) moves.push({ move_uci: moveUci, probability: weights[itemIndex] / total });
    } catch (error) { throw new Error(`Invalid decoded Maia move ${moveUci}: ${error}`); }
  }
  if (moves.length !== originalBoard.moves().length || new Set(moves.map((move) => move.move_uci)).size !== moves.length || !Number.isFinite(total) || total <= 0 || Math.abs(moves.reduce((sum, move) => sum + move.probability, 0) - 1) > 1e-9) throw new Error("Incomplete Maia distribution");
  return moves.sort((left, right) => right.probability - left.probability);
}
