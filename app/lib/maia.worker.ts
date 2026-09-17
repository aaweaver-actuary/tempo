import { runMaiaInference } from "./maia-inference";
import { maiaRequestSchema } from "../domain/schemas";
import { parseData } from "./validated-data";

let tail: Promise<unknown> = Promise.resolve();
let latestRequest = 0;
self.onmessage = ({ data: raw }) => {
  const request = parseData(maiaRequestSchema, raw, "Maia request");
  latestRequest = request.id;
  const result = tail.then(() => {
    if (request.id !== latestRequest)
      throw new DOMException("Superseded position", "AbortError");
    return runMaiaInference(
      request.fen,
      request.elo,
      request.assetRoot,
      (progress) =>
        self.postMessage({ type: "progress", id: request.id, progress }),
    );
  });
  tail = result.catch(() => undefined);
  void result.then(
    (moves) => self.postMessage({ type: "result", id: request.id, moves }),
    (error) =>
      self.postMessage({
        type: "error",
        id: request.id,
        message: String(error),
      }),
  );
};
