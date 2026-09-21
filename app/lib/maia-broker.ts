import {
  analyzeWithMaia,
  MaiaCancelledError,
  type EngineMove,
} from "./analysis-engines";

type MaiaRequest = {
  fen: string;
  elo: number;
  onProgress?: (progress: number) => void;
  resolve: (moves: EngineMove[]) => void;
  reject: (error: unknown) => void;
  background: boolean;
  controller?: AbortController;
  cancelledByCaller?: boolean;
};

const interactiveQueue: MaiaRequest[] = [];
const backgroundQueue: MaiaRequest[] = [];
let activeRequest: MaiaRequest | undefined;
let processing = false;

function processNext() {
  if (processing) return;
  const request = interactiveQueue.shift() ?? backgroundQueue.shift();
  if (!request) return;
  processing = true;
  activeRequest = request;
  request.controller = new AbortController();
  void analyzeWithMaia(
    request.fen,
    request.elo,
    request.onProgress,
    request.controller.signal,
  )
    .then(request.resolve)
    .catch((error) => {
      if (
        error instanceof MaiaCancelledError &&
        request.background &&
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
      activeRequest = undefined;
      processing = false;
      processNext();
    });
}

export function requestBackgroundMaia(
  fen: string,
  elo: number,
  onProgress?: (progress: number) => void,
  signal?: AbortSignal,
) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const request: MaiaRequest = {
      fen,
      elo,
      onProgress,
      resolve,
      reject,
      background: true,
    };
    const cancel = () => {
      request.cancelledByCaller = true;
      const index = backgroundQueue.indexOf(request);
      if (index >= 0) {
        backgroundQueue.splice(index, 1);
        reject(new DOMException("Cancelled", "AbortError"));
      } else if (activeRequest === request) {
        request.controller?.abort();
      }
    };
    signal?.addEventListener("abort", cancel, { once: true });
    backgroundQueue.push(request);
    processNext();
  });
}

export function requestInteractiveMaia(fen: string, elo: number, onProgress?: (progress: number) => void) {
  return new Promise<EngineMove[]>((resolve, reject) => {
    interactiveQueue.push({ fen, elo, onProgress, resolve, reject, background: false });
    if (activeRequest?.background) activeRequest.controller?.abort();
    processNext();
  });
}
