import {
  analyzeWithStockfish,
  StockfishCancelledError,
  type EngineMove,
} from "./analysis-engines";
import { asFenString } from "../types";
import { EnginePriority } from "../types";

type EngineRequest = {
  fen: string;
  depth: number;
  resolve: (moves: EngineMove[]) => void;
  reject: (error: unknown) => void;
  priority: EnginePriority;
  controller?: AbortController;
  cancelledByCaller?: boolean;
  detachCaller?: () => void;
};

const interactiveQueue: EngineRequest[] = [];
const backgroundQueue: EngineRequest[] = [];
let processing = false;
let activeRequest: EngineRequest | undefined;

function processNextRequest() {
  if (processing) return;
  const request = interactiveQueue.shift() ?? backgroundQueue.shift();
  if (!request) return;
  processing = true;
  activeRequest = request;
  request.controller = new AbortController();
  void analyzeWithStockfish(asFenString(request.fen), request.depth, request.controller.signal)
    .then(request.resolve)
    .catch((error) => {
      if (
        error instanceof StockfishCancelledError &&
        request.priority === EnginePriority.Background &&
        !request.cancelledByCaller
      ) {
        backgroundQueue.unshift(request);
        return;
      }
      request.reject(
        request.cancelledByCaller
          ? new DOMException("Cancelled", "AbortError")
          : error,
      );
    })
    .finally(() => {
      request.detachCaller?.();
      activeRequest = undefined;
      processing = false;
      processNextRequest();
    });
}

export function requestBackgroundAnalysis(
  fen: string,
  depth: number,
  signal?: AbortSignal,
) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const request: EngineRequest = {
      fen,
      depth,
      resolve,
      reject,
      priority: EnginePriority.Background,
    };
    const cancel = () => {
      request.cancelledByCaller = true;
      const queuedIndex = backgroundQueue.indexOf(request);
      if (queuedIndex >= 0) {
        backgroundQueue.splice(queuedIndex, 1);
        request.detachCaller?.();
        reject(new DOMException("Cancelled", "AbortError"));
      } else if (activeRequest === request) {
        request.controller?.abort();
      }
    };
    if (signal) {
      signal.addEventListener("abort", cancel, { once: true });
      request.detachCaller = () => signal.removeEventListener("abort", cancel);
    }
    backgroundQueue.push(request);
    processNextRequest();
  });
}

export function requestInteractiveAnalysis(fen: string, depth: number) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    interactiveQueue.push({ fen, depth, resolve, reject, priority: EnginePriority.Interactive });
    if (activeRequest?.priority === EnginePriority.Background) {
      activeRequest.controller?.abort();
    }
    processNextRequest();
  });
}
