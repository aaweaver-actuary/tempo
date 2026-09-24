import { Chess, Move } from "chess.js";
import { assetUrl } from "../const";

import {
  maiaReplySchema,
  engineMoveSchema,
  stockfishMessageSchema,
} from "../domain/schemas";
import { parseData, validRecords } from "./validated-data";
import type { CandidateMove, FenString } from "../types";
import { asSanMove, asUciMove } from "../types";

export type EngineMove = CandidateMove;

export type StockfishRequest = {
  fen: FenString;
  depth: number;
  multipv?: number;
  positionStartFen?: string;
  positionPrefixUci?: string[];
  rootMoveUci?: string;
};

let stockfishWorker: Worker | undefined;
let stockfishRequest = 0;
const STOCKFISH_REQUEST_TIMEOUT_MS = 60_000;

export class StockfishCancelledError extends Error {
  constructor() {
    super("Stockfish analysis was preempted");
    this.name = "StockfishCancelledError";
  }
}

export class MaiaCancelledError extends Error {
  constructor() {
    super("Maia analysis was preempted");
    this.name = "MaiaCancelledError";
  }
}

async function loadStockfish() {
  if (!stockfishWorker)
    stockfishWorker = new Worker(`${assetUrl("stockfish-worker.js")}?v=3`, {
      type: "module",
    });
  return stockfishWorker;
}

export function analyzeWithStockfish(
  fen: FenString,
  depth = 10,
  signal?: AbortSignal,
): Promise<EngineMove[]> {
  return stockfishAnalysis({ fen, depth }, signal);
}

export function analyzeWithStockfishRequest(
  request: StockfishRequest,
  signal?: AbortSignal,
): Promise<EngineMove[]> {
  return stockfishAnalysis(request, signal);
}

async function stockfishAnalysis(
  request: StockfishRequest,
  signal?: AbortSignal,
): Promise<EngineMove[]> {
  const { fen, depth } = request;
  const worker = await loadStockfish();
  const id = ++stockfishRequest;
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new StockfishCancelledError());
      return;
    }
    const lines = new Map<number, EngineMove>();
    const cleanup = () => {
      window.clearTimeout(timeout);
      worker.removeEventListener("message", receive);
      signal?.removeEventListener("abort", cancel);
    };
    const cancel = () => worker.postMessage({ type: "cancel", id });
    const timeout = window.setTimeout(() => {
      cleanup();
      reject(new Error("Stockfish took too long"));
    }, STOCKFISH_REQUEST_TIMEOUT_MS);
    const receive = (event: MessageEvent<unknown>) => {
      const parsed = stockfishMessageSchema.safeParse(event.data);
      if (!parsed.success) return;
      const data = parsed.data;
      if (data.id !== id) return;
      if (data.type === "cancelled") {
        cleanup();
        reject(new StockfishCancelledError());
        return;
      }
      if (data.type === "error") {
        cleanup();
        reject(new Error(data.message ?? "Stockfish could not start"));
        return;
      }
      for (const line of (data.line ?? "")
        .split(/\r?\n/)
        .map((value) => value.trim())
        .filter(Boolean)) {
        if (line.startsWith("info ") && line.includes(" pv ")) {
          const multipv = Number(line.match(/ multipv (\d+)/)?.[1] ?? 1);
          const uci = line.match(/ pv ([a-h][1-8][a-h][1-8][qrbn]?)/)?.[1];
          if (!uci) return;
          const mate = line.match(/ score mate (-?\d+)/)?.[1];
          const cp = line.match(/ score cp (-?\d+)/)?.[1];
          const completedDepth = Number(line.match(/ depth (\d+)/)?.[1] ?? 0);
          const chess = new Chess(fen);
          let move: Move | null = null;
          try {
            move = chess.move({
              from: uci.slice(0, 2),
              to: uci.slice(2, 4),
              promotion: uci[4] || "q",
            });
          } catch {
            return;
          }
          const score = mate
            ? `M${mate}`
            : cp
              ? `${Number(cp) >= 0 ? "+" : ""}${(Number(cp) / 100).toFixed(2)}`
              : "—";
          const pv = (line.split(" pv ")[1]?.trim().split(/\s+/) ?? [uci]).map(
            asUciMove,
          );
          lines.set(multipv, {
            uci: asUciMove(uci),
            san: asSanMove(move.san),
            score,
            pv,
            cp: cp === undefined ? undefined : Number(cp),
            mate: mate === undefined ? undefined : Number(mate),
            depth: completedDepth,
          });
        }
        if (line.startsWith("bestmove ")) {
          cleanup();
          resolve(
            [...lines.entries()]
              .sort(([a], [b]) => a - b)
              .map(([, move]) => move),
          );
        }
      }
    };
    worker.addEventListener("message", receive);
    signal?.addEventListener("abort", cancel, { once: true });
    worker.postMessage({
      type: "analyze", id, fen, depth,
      multipv: request.multipv ?? 5,
      positionStartFen: request.positionStartFen,
      positionPrefixUci: request.positionPrefixUci,
      rootMoveUci: request.rootMoveUci,
    });
  });
}

let maiaWorker: Worker | undefined;
let maiaId = 0;
const maiaRequests = new Map<
  number,
  {
    resolve: (moves: EngineMove[]) => void;
    reject: (error: Error) => void;
    progress?: (progress: number) => void;
  }
>();

export function analyzeWithMaia(
  fen: string,
  elo: number,
  onProgress?: (progress: number) => void,
  signal?: AbortSignal,
): Promise<EngineMove[]> {
  if (typeof Worker === "undefined")
    return Promise.reject(new Error("Maia requires browser workers"));
  if (!maiaWorker) {
    maiaWorker = new Worker(new URL("./maia.worker.ts", import.meta.url), {
      type: "module",
    });
    maiaWorker.onmessage = ({ data: raw }) => {
      let reply;
      try {
        reply = parseData(maiaReplySchema, raw, "Maia worker");
      } catch (error) {
        failMaia(error instanceof Error ? error : new Error(String(error)));
        return;
      }
      const request = maiaRequests.get(reply.id);
      if (!request) return;
      if (reply.type === "progress") request.progress?.(reply.progress!);
      else {
        maiaRequests.delete(reply.id);
        if (reply.type === "error") request.reject(new Error(reply.message));
        else
          request.resolve(
            validRecords(engineMoveSchema, reply.moves!, "Maia move").flatMap(
              (move) =>
                move.san
                  ? [
                      {
                        ...move,
                        san: asSanMove(move.san),
                        pv: move.pv?.flatMap((uci) => {
                          try {
                            return [asUciMove(uci)];
                          } catch {
                            return [];
                          }
                        }),
                      },
                    ]
                  : [],
            ),
          );
      }
    };
    maiaWorker.onerror = () =>
      failMaia(new Error("Maia worker failed. Toggle Maia to retry."));
  }
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new MaiaCancelledError());
      return;
    }
    const id = ++maiaId;
    const cancel = () => {
      signal?.removeEventListener("abort", cancel);
      failMaia(new MaiaCancelledError());
    };
    signal?.addEventListener("abort", cancel, { once: true });
    maiaRequests.set(id, {
      resolve: (moves) => {
        signal?.removeEventListener("abort", cancel);
        resolve(moves);
      },
      reject: (error) => {
        signal?.removeEventListener("abort", cancel);
        reject(error);
      },
      progress: onProgress,
    });
    maiaWorker!.postMessage({
      id,
      fen,
      elo,
      assetRoot: new URL("./", assetUrl("index.html")).href,
    });
  });
}

function failMaia(error: Error) {
  for (const request of maiaRequests.values()) request.reject(error);
  maiaRequests.clear();
  maiaWorker?.terminate();
  maiaWorker = undefined;
}
