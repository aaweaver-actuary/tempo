import { Chess, Move } from 'chess.js';
import * as ort from 'onnxruntime-web/wasm';
import { assetUrl } from '../const';

import moveIndex from './all_moves_maia3.json';
import reversedMoveIndex from './all_moves_maia3_reversed.json';

export type EngineMove = {
  uci: string;
  san: string;
  score?: string;
  probability?: number;
  cp?: number;
  mate?: number;
};

let stockfishWorker: Worker | undefined;
let stockfishRequest = 0;

async function loadStockfish() {
  if (!stockfishWorker) stockfishWorker = new Worker(`${assetUrl('stockfish-worker.js')}?v=2`, { type: 'module' });
  return stockfishWorker;
}

export async function analyzeWithStockfish(fen: string): Promise<EngineMove[]> {
  const worker = await loadStockfish();
  const id = ++stockfishRequest;
  return new Promise((resolve, reject) => {
    const lines = new Map<number, EngineMove>();
    const timeout = window.setTimeout(() => {
      worker.removeEventListener('message', receive);
      reject(new Error('Stockfish took too long'));
    }, 60_000);
    const receive = (event: MessageEvent<{ type: string; id?: number; line?: string; message?: string }>) => {
      if (event.data.id !== id) return;
      if (event.data.type === 'error') {
        window.clearTimeout(timeout);
        worker.removeEventListener('message', receive);
        reject(new Error(event.data.message ?? 'Stockfish could not start'));
        return;
      }
      for (const line of (event.data.line ?? '').split(/\r?\n/).map((value) => value.trim()).filter(Boolean)) {
      if (line.startsWith('info ') && line.includes(' pv ')) {
        const multipv = Number(line.match(/ multipv (\d+)/)?.[1] ?? 1);
        const uci = line.match(/ pv ([a-h][1-8][a-h][1-8][qrbn]?)/)?.[1];
        if (!uci) return;
        const mate = line.match(/ score mate (-?\d+)/)?.[1];
        const cp = line.match(/ score cp (-?\d+)/)?.[1];
        const chess = new Chess(fen);
        let move: Move | null = null;
        try { move = chess.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || 'q' }); } catch { return; }
        const score = mate ? `M${mate}` : cp ? `${Number(cp) >= 0 ? '+' : ''}${(Number(cp) / 100).toFixed(2)}` : '—';
        lines.set(multipv, { uci, san: move.san, score, cp: cp === undefined ? undefined : Number(cp), mate: mate === undefined ? undefined : Number(mate) });
      }
      if (line.startsWith('bestmove ')) {
        window.clearTimeout(timeout);
        worker.removeEventListener('message', receive);
        resolve([...lines.entries()].sort(([a], [b]) => a - b).map(([, move]) => move));
      }
      }
    };
    worker.addEventListener('message', receive);
    worker.postMessage({ type: 'analyze', id, fen, depth: 10 });
  });
}

const allMoves = moveIndex as Record<string, number>;
const reversedMoves = reversedMoveIndex as Record<string, string>;

function mirrorSquare(square: string) {
  return `${square[0]}${9 - Number(square[1])}`;
}

function mirrorMove(uci: string) {
  return `${mirrorSquare(uci.slice(0, 2))}${mirrorSquare(uci.slice(2, 4))}${uci.slice(4)}`;
}

function mirrorFen(fen: string) {
  const [placement, active, castling, ep, halfmove, fullmove] = fen.split(' ');
  const swap = (rank: string) => [...rank].map((char) => /[a-z]/.test(char) ? char.toUpperCase() : /[A-Z]/.test(char) ? char.toLowerCase() : char).join('');
  const rights = castling === '-' ? '-' : [castling.includes('k') ? 'K' : '', castling.includes('q') ? 'Q' : '', castling.includes('K') ? 'k' : '', castling.includes('Q') ? 'q' : ''].join('') || '-';
  return `${placement.split('/').reverse().map(swap).join('/')} ${active === 'w' ? 'b' : 'w'} ${rights} ${ep === '-' ? '-' : mirrorSquare(ep)} ${halfmove} ${fullmove}`;
}

function preprocessMaia(fen: string) {
  const isBlack = fen.split(' ')[1] === 'b';
  const normalizedFen = isBlack ? mirrorFen(fen) : fen;
  const chess = new Chess(normalizedFen);
  const pieceTypes = ['P', 'N', 'B', 'R', 'Q', 'K', 'p', 'n', 'b', 'r', 'q', 'k'];
  const tokens = new Float32Array(64 * 12);
  normalizedFen.split(' ')[0].split('/').forEach((rankText, rankFromTop) => {
    let file = 0;
    for (const char of rankText) {
      if (/\d/.test(char)) file += Number(char);
      else {
        const square = (7 - rankFromTop) * 8 + file;
        tokens[square * 12 + pieceTypes.indexOf(char)] = 1;
        file += 1;
      }
    }
  });
  const legalMoves = new Float32Array(Object.keys(allMoves).length);
  for (const move of chess.moves({ verbose: true })) {
    const uci = `${move.from}${move.to}${move.promotion ?? ''}`;
    const index = allMoves[uci];
    if (index !== undefined) legalMoves[index] = 1;
  }
  return { tokens, legalMoves, isBlack };
}

let maiaReady: Promise<ort.InferenceSession> | undefined;
const MAIA_INITIALIZATION_TIMEOUT_MS = 45_000;

function withTimeout<T>(promise: Promise<T>, milliseconds: number, message: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error(message)), milliseconds);
    promise.then((value) => {
      window.clearTimeout(timeout);
      resolve(value);
    }, (error) => {
      window.clearTimeout(timeout);
      reject(error);
    });
  });
}

function loadMaia(onProgress?: (progress: number) => void) {
  if (!maiaReady) maiaReady = (async () => {
    ort.env.wasm.wasmPaths = {
      mjs: assetUrl('ort/ort-wasm-simd-threaded.mjs'),
      wasm: assetUrl('ort/ort-wasm-simd-threaded.wasm'),
    };
    ort.env.wasm.numThreads = crossOriginIsolated ? Math.min(2, navigator.hardwareConcurrency || 1) : 1;
    onProgress?.(5);
    const chunks: Uint8Array[] = [];
    for (let index=0; index<6; index++) {
      const response = await fetch(assetUrl(`maia3/parts/part-${String(index).padStart(2,'0')}`));
      if (!response.ok) throw new Error('Could not load Maia model');
      chunks.push(new Uint8Array(await response.arrayBuffer())); onProgress?.(Math.round((index+1)/6*90));
    }
    const data = new Uint8Array(chunks.reduce((sum,chunk)=>sum+chunk.length,0)); let offset=0;
    for (const chunk of chunks) { data.set(chunk,offset); offset+=chunk.length; }
    const session = await withTimeout(
      ort.InferenceSession.create(data, { executionProviders: ['wasm'] }),
      MAIA_INITIALIZATION_TIMEOUT_MS,
      'Maia initialization timed out. Check that the ONNX Runtime and model versions match, then retry.',
    );
    onProgress?.(100);
    return session;
  })().catch((error) => {
    maiaReady = undefined;
    throw error;
  });
  return maiaReady;
}

export async function analyzeWithMaia(fen: string, elo: number, onProgress?: (progress: number) => void): Promise<EngineMove[]> {
  const session = await loadMaia(onProgress);
  const { tokens, legalMoves, isBlack } = preprocessMaia(fen);
  const result = await session.run({ tokens: new ort.Tensor('float32', tokens, [1,64,12]), elo_self: new ort.Tensor('float32', Float32Array.of(elo), [1]), elo_oppo: new ort.Tensor('float32', Float32Array.of(elo), [1]) });
      const logits = result.logits_move.data as Float32Array;
      const legalIndices = [...legalMoves.keys()].filter((index) => legalMoves[index] > 0);
      const max = Math.max(...legalIndices.map((index) => logits[index]));
      const weights = legalIndices.map((index) => Math.exp(logits[index] - max));
      const total = weights.reduce((sum, value) => sum + value, 0);
      const position = new Chess(fen);
      const moves: EngineMove[] = [];
      for (const [itemIndex, index] of legalIndices.entries()) {
        const raw = reversedMoves[String(index)];
        const uci = isBlack ? mirrorMove(raw) : raw;
        let move: Move | null = null;
        try {
          move = position.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || 'q' });
          position.undo();
        } catch { /* filtered below */ }
        if (!move) continue;
        moves.push({
          uci,
          san: move.san,
          probability: weights[itemIndex] / total,
        });
      }
      return moves.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0));
}
