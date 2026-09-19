import { explorerResponseSchema } from "../domain/schemas";
import { parseData } from "./validated-data";

type ExplorerPayload = {
  human: unknown;
  masters: unknown;
  fetchedAt: number;
};

const CACHE_VERSION = 1;
const CACHE_PREFIX = `tempo-explorer-cache-v${CACHE_VERSION}:`;
const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1_000;
const requests = new Map<string, Promise<ExplorerPayload>>();

function key(fen: string, speeds: string, ratings: string) {
  return `${fen}|${speeds}|${ratings}`;
}

export function readCachedExplorer(
  fen: string,
  speeds: string,
  ratings: string,
): ExplorerPayload | undefined {
  try {
    const raw = localStorage.getItem(`${CACHE_PREFIX}${key(fen, speeds, ratings)}`);
    if (!raw) return undefined;
    const parsed = JSON.parse(raw) as ExplorerPayload;
    if (Date.now() - parsed.fetchedAt > CACHE_MAX_AGE_MS) return undefined;
    parseData(explorerResponseSchema, parsed.human, "cached Lichess explorer");
    parseData(explorerResponseSchema, parsed.masters, "cached Masters explorer");
    return parsed;
  } catch {
    return undefined;
  }
}

export function loadExplorer(
  fen: string,
  speeds: string,
  ratings: string,
): Promise<ExplorerPayload> {
  const requestKey = key(fen, speeds, ratings);
  const existing = requests.get(requestKey);
  if (existing) return existing;
  const base = "https://explorer.lichess.org";
  const request = Promise.all([
    fetch(
      `${base}/lichess?variant=standard&speeds=${speeds}&ratings=${ratings}&fen=${encodeURIComponent(fen)}`,
    ),
    fetch(`${base}/masters?variant=standard&fen=${encodeURIComponent(fen)}`),
  ])
    .then(async ([lichess, masters]) => {
      if (lichess.status === 429 || masters.status === 429)
        throw new Error("rate-limited");
      if (!lichess.ok || !masters.ok) throw new Error("upstream-failure");
      return {
        human: await lichess.json(),
        masters: await masters.json(),
        fetchedAt: Date.now(),
      };
    })
    .then((payload) => {
      parseData(explorerResponseSchema, payload.human, "Lichess explorer");
      parseData(explorerResponseSchema, payload.masters, "Masters explorer");
      localStorage.setItem(
        `${CACHE_PREFIX}${requestKey}`,
        JSON.stringify(payload),
      );
      return payload;
    })
    .finally(() => requests.delete(requestKey));
  requests.set(requestKey, request);
  return request;
}
