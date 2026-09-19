import { analyzeWithStockfish, type EngineMove } from "./analysis-engines";

type EngineRequest = {
  fen: string;
  depth: number;
  resolve: (moves: EngineMove[]) => void;
  reject: (error: unknown) => void;
};

const interactiveQueue: EngineRequest[] = [];
const backgroundQueue: EngineRequest[] = [];
let processing = false;

function processNextRequest() {
  if (processing) return;
  const request = interactiveQueue.shift() ?? backgroundQueue.shift();
  if (!request) return;
  processing = true;
  void analyzeWithStockfish(request.fen, request.depth)
    .then(request.resolve, request.reject)
    .finally(() => {
      processing = false;
      processNextRequest();
    });
}

export function requestBackgroundAnalysis(fen: string, depth: number) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    backgroundQueue.push({ fen, depth, resolve, reject });
    processNextRequest();
  });
}

export function requestInteractiveAnalysis(fen: string, depth: number) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    interactiveQueue.push({ fen, depth, resolve, reject });
    processNextRequest();
  });
}
