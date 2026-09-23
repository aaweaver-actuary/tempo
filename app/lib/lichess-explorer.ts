import { explorerResponseSchema } from "../domain/schemas";

export type ExplorerSourceName = "lichess" | "masters";
export type ExplorerFailureCategory =
  | "authentication-required"
  | "authentication-failed"
  | "rate-limited"
  | "offline"
  | "upstream-unavailable"
  | "invalid-response"
  | "network-error";
export type ExplorerSourceState =
  | "off"
  | "loading"
  | "ready"
  | "stale"
  | ExplorerFailureCategory;

export type ExplorerSourceResult = {
  source: ExplorerSourceName;
  state: ExplorerSourceState;
  moves: unknown[];
  status?: number;
  category?: ExplorerFailureCategory;
  message?: string;
  retryable: boolean;
  hasCachedData: boolean;
};

export type ExplorerResult = {
  lichess: ExplorerSourceResult;
  masters: ExplorerSourceResult;
};

type CachedSource = { payload: unknown; fetchedAt: number };
type CachedExplorer = { human: CachedSource; masters: CachedSource };

const CACHE_VERSION = 2;
const CACHE_PREFIX = `tempo-explorer-cache-v${CACHE_VERSION}:`;
const LEGACY_CACHE_PREFIX = "tempo-explorer-cache-v1:";
const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1_000;
const requests = new Map<string, { accessToken: string; promise: Promise<ExplorerResult> }>();

function key(fen: string, speeds: string, ratings: string) {
  return `${fen}|${speeds}|${ratings}`;
}

function readCachedSource(raw: string | null): CachedSource | undefined {
  if (!raw) return undefined;
  const parsed = JSON.parse(raw) as CachedSource;
  if (!parsed || typeof parsed.fetchedAt !== "number") return undefined;
  if (Date.now() - parsed.fetchedAt > CACHE_MAX_AGE_MS) return undefined;
  if (!explorerResponseSchema.safeParse(parsed.payload).success) return undefined;
  return parsed;
}

function readCache(cacheKey: string): CachedExplorer | undefined {
  try {
    const cachedHuman = readCachedSource(localStorage.getItem(`${CACHE_PREFIX}${cacheKey}:lichess`));
    const cachedMasters = readCachedSource(localStorage.getItem(`${CACHE_PREFIX}${cacheKey}:masters`));
    if (cachedHuman || cachedMasters)
      return {
        human: cachedHuman ?? { payload: { moves: [] }, fetchedAt: 0 },
        masters: cachedMasters ?? { payload: { moves: [] }, fetchedAt: 0 },
      };

    // Preserve still-fresh entries written by the previous paired cache format.
    const legacy = localStorage.getItem(`${LEGACY_CACHE_PREFIX}${cacheKey}`);
    if (!legacy) return undefined;
    const parsed = JSON.parse(legacy) as { human: unknown; masters: unknown; fetchedAt: number };
    if (Date.now() - parsed.fetchedAt > CACHE_MAX_AGE_MS) return undefined;
    if (!explorerResponseSchema.safeParse(parsed.human).success ||
        !explorerResponseSchema.safeParse(parsed.masters).success) return undefined;
    const migrated = {
      human: { payload: parsed.human, fetchedAt: parsed.fetchedAt },
      masters: { payload: parsed.masters, fetchedAt: parsed.fetchedAt },
    };
    localStorage.setItem(`${CACHE_PREFIX}${cacheKey}:lichess`, JSON.stringify(migrated.human));
    localStorage.setItem(`${CACHE_PREFIX}${cacheKey}:masters`, JSON.stringify(migrated.masters));
    return migrated;
  } catch {
    return undefined;
  }
}

export function readCachedExplorer(
  fen: string,
  speeds: string,
  ratings: string,
): { human: unknown; masters: unknown } | undefined {
  const cached = readCache(key(fen, speeds, ratings));
  if (!cached) return undefined;
  return {
    human: cached.human.fetchedAt ? cached.human.payload : undefined,
    masters: cached.masters.fetchedAt ? cached.masters.payload : undefined,
  };
}

function result(
  source: ExplorerSourceName,
  state: ExplorerSourceState,
  cached: CachedSource | undefined,
  status?: number,
  message?: string,
  retryable = false,
  freshMoves?: unknown[],
): ExplorerSourceResult {
  const hasCachedData = Boolean(cached?.fetchedAt);
  const useCache = hasCachedData && (state !== "ready");
  const payload = useCache ? cached?.payload : undefined;
  const moves = freshMoves ?? (payload && typeof payload === "object" && "moves" in payload && Array.isArray(payload.moves)
    ? payload.moves
    : []);
  return {
    source,
    state: useCache ? "stale" : state,
    moves,
    status,
    category: state === "stale" ? undefined : state in {
      "authentication-required": true,
      "authentication-failed": true,
      "rate-limited": true,
      offline: true,
      "upstream-unavailable": true,
      "invalid-response": true,
      "network-error": true,
    } ? state as ExplorerFailureCategory : undefined,
    message: useCache ? `${message ?? "Refresh failed"}; showing cached data.` : message,
    retryable,
    hasCachedData,
  };
}

function failureCategory(response: Response): ExplorerFailureCategory {
  if (response.status === 401 || response.status === 403)
    return "authentication-failed";
  if (response.status === 429) return "rate-limited";
  if (response.status === 502 || response.status === 503 || response.status === 504 || response.status >= 500)
    return "upstream-unavailable";
  return "upstream-unavailable";
}

async function fetchSource(
  source: ExplorerSourceName,
  url: string,
  token: string,
  cached: CachedSource | undefined,
  cacheKey: string,
): Promise<ExplorerSourceResult> {
  let response: Response;
  try {
    response = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  } catch {
    return result(source, navigator.onLine ? "network-error" : "offline", cached,
      undefined, navigator.onLine ? "Network request failed" : "Offline", true);
  }
  if (!response.ok) {
    const category = failureCategory(response);
    const retryable = category === "rate-limited" || category === "upstream-unavailable";
    const detail = `${source === "masters" ? "Masters" : "Lichess"} Explorer ${category.replaceAll("-", " ")} (HTTP ${response.status})`;
    return result(source, category, cached, response.status, detail, retryable);
  }
  let parsedPayload: ReturnType<typeof explorerResponseSchema.safeParse>;
  try {
    const payload: unknown = await response.json();
    parsedPayload = explorerResponseSchema.safeParse(payload);
    if (!parsedPayload.success) throw new Error("invalid-response");
  } catch {
    return result(source, "invalid-response", cached, response.status,
      `${source === "masters" ? "Masters" : "Lichess"} Explorer returned an invalid response`, false);
  }
  try {
    localStorage.setItem(`${CACHE_PREFIX}${cacheKey}:${source}`, JSON.stringify({ payload: parsedPayload.data, fetchedAt: Date.now() }));
  } catch {
    // A full or unavailable browser cache must not turn a successful source into a failure.
  }
  return result(source, "ready", undefined, undefined, undefined, false,
    parsedPayload.data.moves);
}

export function loadExplorer(
  fen: string,
  speeds: string,
  ratings: string,
  accessToken: string,
): Promise<ExplorerResult> {
  const cacheKey = key(fen, speeds, ratings);
  const existing = requests.get(cacheKey);
  if (existing?.accessToken === accessToken) return existing.promise;
  const cached = readCache(cacheKey);
  if (!accessToken) {
    const unauthenticated = Promise.resolve({
      lichess: result("lichess", "authentication-required", cached?.human, undefined, "Sign in to Lichess to load Explorer data"),
      masters: result("masters", "authentication-required", cached?.masters, undefined, "Sign in to Lichess to load Explorer data"),
    });
    return unauthenticated;
  }
  const base = "https://explorer.lichess.org";
  const request = { accessToken, promise: Promise.resolve({} as ExplorerResult) };
  request.promise = Promise.all([
    fetchSource(
      "lichess",
      `${base}/lichess?variant=standard&speeds=${encodeURIComponent(speeds)}&ratings=${encodeURIComponent(ratings)}&fen=${encodeURIComponent(fen)}`,
      accessToken,
      cached?.human,
      cacheKey,
    ),
    fetchSource(
      "masters",
      `${base}/masters?variant=standard&fen=${encodeURIComponent(fen)}`,
      accessToken,
      cached?.masters,
      cacheKey,
    ),
  ]).then(([lichess, masters]) => ({ lichess, masters }))
    .finally(() => {
      if (requests.get(cacheKey) === request) requests.delete(cacheKey);
    });
  requests.set(cacheKey, request);
  return request.promise;
}
