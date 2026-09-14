import { Chess, Move } from 'chess.js';
import * as ort from 'onnxruntime-web/wasm';

import moveIndex from './all_moves_maia3.json';
import reversedMoveIndex from './all_moves_maia3_reversed.json';

export type EngineMove = {
  uci: string;
  san: string;
  score?: string;
  probability?: number;
};

type StockfishModule = { uci: (command: string) => void; listen: (line: string) => void; setNnueBuffer: (data: Uint8Array) => void };
let stockfishEngine: StockfishModule | undefined;
let stockfishReady: Promise<StockfishModule> | undefined;

async function loadStockfish() {
  if (!stockfishReady) {
    stockfishReady = (async () => {
      const source = await fetch('/engines/sf_19_smallnet.js').then((response) => response.text());
      const scriptBlob = new Blob([source], { type: 'text/javascript' });
      const moduleUrl = URL.createObjectURL(scriptBlob);
      const module = await import(/* @vite-ignore */ moduleUrl);
      const engine = await module.default({ locateFile: (file: string) => `/engines/${file}`, mainScriptUrlOrBlob: scriptBlob, listen: () => undefined, onError: (message: string) => console.error(message) }) as StockfishModule;
      URL.revokeObjectURL(moduleUrl);
      const response = await fetch('/engines/nn-61e7af4bb97d.nnue');
      if (!response.ok) throw new Error('Could not load Stockfish evaluation network');
      engine.setNnueBuffer(new Uint8Array(await response.arrayBuffer()));
      engine.uci('uci'); engine.uci('setoption name Threads value 1'); engine.uci('setoption name Hash value 32'); engine.uci('setoption name MultiPV value 3');
      stockfishEngine = engine;
      return engine;
    })();
  }
  return stockfishReady;
}

export async function analyzeWithStockfish(fen: string): Promise<EngineMove[]> {
  const engine = await loadStockfish();
  return new Promise((resolve, reject) => {
    const lines = new Map<number, EngineMove>();
    const timeout = window.setTimeout(() => reject(new Error('Stockfish took too long')), 15000);
    engine.listen = (line: string) => {
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
        lines.set(multipv, { uci, san: move.san, score });
      }
      if (line.startsWith('bestmove ')) {
        window.clearTimeout(timeout);
        resolve([...lines.entries()].sort(([a], [b]) => a - b).map(([, move]) => move));
      }
    };
    engine.uci('stop'); engine.uci(`position fen ${fen}`); engine.uci('go depth 13');
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
function loadMaia(onProgress?: (progress: number) => void) {
  if (!maiaReady) maiaReady = (async () => {
    ort.env.wasm.wasmPaths = '/ort/'; ort.env.wasm.numThreads = Math.min(2, navigator.hardwareConcurrency || 1);
    onProgress?.(5);
    const response = await fetch('/maia3/maia3_simplified.onnx');
    if (!response.ok) throw new Error('Could not load Maia model');
    const reader = response.body?.getReader(); const chunks: Uint8Array[] = []; let received = 0; const total = Number(response.headers.get('content-length') || 0);
    if (reader) { while (true) { const { done, value } = await reader.read(); if (done) break; chunks.push(value); received += value.length; if (total) onProgress?.(Math.min(90, Math.round(received / total * 90))); } }
    const data = reader ? new Uint8Array(received) : new Uint8Array(await response.arrayBuffer());
    if (reader) { let offset=0; for (const chunk of chunks) { data.set(chunk,offset); offset+=chunk.length; } }
    const session = await ort.InferenceSession.create(data, { executionProviders: ['wasm'] }); onProgress?.(100); return session;
  })();
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
      const moves = legalIndices.map((index, itemIndex) => {
        const raw = reversedMoves[String(index)];
        const uci = isBlack ? mirrorMove(raw) : raw;
        let move: Move | null = null;
        try { move = position.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] || 'q' }); position.undo(); } catch { /* filtered below */ }
        return move ? { uci, san: move.san, probability: weights[itemIndex] / total } : undefined;
      }).filter((move): move is EngineMove => Boolean(move));
      return moves.sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0));
}
