import { Chess, Move } from "chess.js";
import { assetUrl } from "../const";

import {
  maiaReplySchema,
  engineMoveSchema,
  stockfishMessageSchema,
} from "../domain/schemas";
import { parseData, validRecords } from "./validated-data";

export type EngineMove = {
  uci: string;
  san: string;
  score?: string;
  probability?: number;
  cp?: number;
  mate?: number;
  pv?: string[];
};

let stockfishWorker: Worker | undefined;
let stockfishRequest = 0;

async function loadStockfish() {
  if (!stockfishWorker)
    stockfishWorker = new Worker(`${assetUrl("stockfish-worker.js")}?v=2`, {
      type: "module",
    });
  return stockfishWorker;
}

let stockfishTail: Promise<unknown> = Promise.resolve();

export function analyzeWithStockfish(
  fen: string,
  depth = 10,
): Promise<EngineMove[]> {
  const result = stockfishTail.then(() => stockfishAnalysis(fen, depth));
  stockfishTail = result.catch(() => undefined);
  return result;
}

async function stockfishAnalysis(
  fen: string,
  depth: number,
): Promise<EngineMove[]> {
  const worker = await loadStockfish();
  const id = ++stockfishRequest;
  return new Promise((resolve, reject) => {
    const lines = new Map<number, EngineMove>();
    const timeout = window.setTimeout(() => {
      worker.removeEventListener("message", receive);
      reject(new Error("Stockfish took too long"));
    }, 60_000);
    const receive = (event: MessageEvent<unknown>) => {
      const parsed = stockfishMessageSchema.safeParse(event.data);
      if (!parsed.success) return;
      const data = parsed.data;
      if (data.id !== id) return;
      if (data.type === "error") {
        window.clearTimeout(timeout);
        worker.removeEventListener("message", receive);
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
          const pv = line.split(" pv ")[1]?.trim().split(/\s+/) ?? [uci];
          lines.set(multipv, {
            uci,
            san: move.san,
            score,
            pv,
            cp: cp === undefined ? undefined : Number(cp),
            mate: mate === undefined ? undefined : Number(mate),
          });
        }
        if (line.startsWith("bestmove ")) {
          window.clearTimeout(timeout);
          worker.removeEventListener("message", receive);
          resolve(
            [...lines.entries()]
              .sort(([a], [b]) => a - b)
              .map(([, move]) => move),
          );
        }
      }
    };
    worker.addEventListener("message", receive);
    worker.postMessage({ type: "analyze", id, fen, depth });
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
              (move) => (move.san ? [{ ...move, san: move.san }] : []),
            ),
          );
      }
    };
    maiaWorker.onerror = () =>
      failMaia(new Error("Maia worker failed. Toggle Maia to retry."));
  }
  return new Promise((resolve, reject) => {
    const id = ++maiaId;
    maiaRequests.set(id, { resolve, reject, progress: onProgress });
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
