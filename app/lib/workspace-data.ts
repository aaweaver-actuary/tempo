import { API_URL, assetUrl } from "../const";
import type { PackagedPuzzle, PracticeCard, View } from "../types";
import { usesLocalApi } from "../utils/local";
import { runStudyTask } from "./background-study";
import * as z from "zod";
import { tacticProgressSchema } from "../domain/schemas";
import { clearDataDiagnosticsForSources, parseData } from "./validated-data";

const requests = new Map<
  string,
  { createdAt: number; promise: Promise<unknown>; staleValue?: unknown }
>();
const WORKSPACE_CACHE_VERSION = 2;
const WORKSPACE_CACHE_PREFIX = `tempo-workspace-cache-v${WORKSPACE_CACHE_VERSION}:`;
const WORKSPACE_CACHE_LIMIT = 40;

type StoredWorkspaceValue = {
  version: number;
  savedAt: number;
  data: unknown;
};

function canPersist(url: string) {
  return typeof localStorage !== "undefined" && url.includes("/api/");
}

function readPersisted(url: string): StoredWorkspaceValue | undefined {
  if (!canPersist(url)) return undefined;
  try {
    const raw = localStorage.getItem(`${WORKSPACE_CACHE_PREFIX}${url}`);
    if (!raw) return undefined;
    const value = JSON.parse(raw) as StoredWorkspaceValue;
    return value.version === WORKSPACE_CACHE_VERSION ? value : undefined;
  } catch {
    return undefined;
  }
}

function persist(url: string, data: unknown) {
  if (!canPersist(url)) return;
  try {
    localStorage.setItem(
      `${WORKSPACE_CACHE_PREFIX}${url}`,
      JSON.stringify({ version: WORKSPACE_CACHE_VERSION, savedAt: Date.now(), data }),
    );
    const keys = Array.from({ length: localStorage.length }, (_, index) =>
      localStorage.key(index),
    ).filter((key): key is string => Boolean(key?.startsWith(WORKSPACE_CACHE_PREFIX)));
    if (keys.length > WORKSPACE_CACHE_LIMIT) {
      const oldest = keys
        .map((key) => ({ key, value: JSON.parse(localStorage.getItem(key) ?? "{}") as Partial<StoredWorkspaceValue> }))
        .sort((left, right) => (left.value.savedAt ?? 0) - (right.value.savedAt ?? 0));
      for (const item of oldest.slice(0, keys.length - WORKSPACE_CACHE_LIMIT))
        localStorage.removeItem(item.key);
    }
  } catch {
    // A full or disabled browser store must not prevent live reads.
  }
}

function notifyWorkspaceData(state: "refreshing" | "ready" | "error", url: string) {
  if (typeof window !== "undefined")
    window.dispatchEvent(new CustomEvent("tempo-workspace-data", { detail: { state, url } }));
}

function clearResolvedDiagnostics(url: string) {
  const path = new URL(url, "http://tempo.local").pathname;
  if (path === "/api/games/summary")
    clearDataDiagnosticsForSources(["games", "game"]);
}
const decks = new Map<
  string,
  Promise<Array<{ record: PackagedPuzzle; card: PracticeCard }>>
>();

export function readWorkspaceData(url: string): Promise<unknown>;
export function readWorkspaceData<T>(
  url: string,
  schema: z.ZodType<T>,
): Promise<T>;
export function readWorkspaceData(
  url: string,
  schema?: z.ZodType,
): Promise<unknown> {
  const cached = requests.get(url);
  if (cached && Date.now() - cached.createdAt < 30_000) {
    const value = cached.staleValue === undefined
      ? cached.promise
      : Promise.resolve(cached.staleValue);
    return value;
  }
  const persisted = readPersisted(url);
  const requestController = new AbortController();
  const requestTimeout = setTimeout(
    () =>
      requestController.abort(
        new Error(
          "Service request timed out after 15 seconds. Retry when the service is available.",
        ),
      ),
    15_000,
  );
  if (persisted) notifyWorkspaceData("refreshing", url);
  const entry: { createdAt: number; promise: Promise<unknown>; staleValue?: unknown } = {
    createdAt: Date.now(),
    promise: Promise.resolve(undefined),
    staleValue: persisted?.data,
  };
  const promise = fetch(url, { signal: requestController.signal })
    .then((response) => {
      if (!response.ok)
        throw new Error(
          `Could not load ${new URL(url, document.baseURI).pathname} (HTTP ${response.status}).`,
        );
      return response
        .json()
        .then((raw: unknown) =>
          url.includes("/api/")
            ? runStudyTask<unknown>({ kind: "workspace", url, payload: raw })
            : raw,
        );
    })
    .then((raw) => {
      const validated = schema ? parseData(schema, raw, url) : raw;
      entry.staleValue = undefined;
      clearResolvedDiagnostics(url);
      persist(url, validated);
      notifyWorkspaceData("ready", url);
      return validated;
    })
    .catch((error) => {
      requests.delete(url);
      notifyWorkspaceData("error", url);
      throw error;
    })
    .finally(() => clearTimeout(requestTimeout));
  entry.promise = promise;
  requests.set(url, entry);
  if (persisted) {
    void promise.catch(() => undefined);
    try {
      return Promise.resolve(
        schema ? parseData(schema, persisted.data, url) : persisted.data,
      );
    } catch {
      localStorage.removeItem(`${WORKSPACE_CACHE_PREFIX}${url}`);
      return promise;
    }
  }
  return promise;
}

export function invalidateWorkspaceData() {
  requests.clear();
  if (typeof localStorage !== "undefined") {
    for (let index = localStorage.length - 1; index >= 0; index--) {
      const key = localStorage.key(index);
      if (key?.startsWith(WORKSPACE_CACHE_PREFIX)) localStorage.removeItem(key);
    }
  }
}
export async function readWorkspaceResponse(url: string) {
  return Response.json(await readWorkspaceData(url));
}
export function resetWorkspaceCache() {
  requests.clear();
  decks.clear();
}

export function loadTacticsDeck(motif: string, stage: string) {
  const deckId = `${motif}-${stage}`;
  let deck = decks.get(deckId);
  if (!deck) {
    deck = readWorkspaceData(
      assetUrl(
        /-\d{2}$/.test(deckId)
          ? `data/tactics-packs/${deckId}.json`
          : "data/tactics-decks.json",
      ),
      z.array(z.unknown()),
    )
      .then((records) =>
        runStudyTask<Array<{ record: PackagedPuzzle; card: PracticeCard }>>({
          kind: "deck",
          records,
          deckId,
        }),
      )
      .catch((error) => {
        decks.delete(deckId);
        throw error;
      });
    decks.set(deckId, deck);
  }
  return deck;
}

export async function preloadView(view: View) {
  if (view === "tactics") {
    if (usesLocalApi())
      await readWorkspaceData(
        `${API_URL}/api/tactics/progress`,
        tacticProgressSchema,
      );
    const selectedPackId =
      (typeof localStorage === "undefined"
        ? undefined
        : localStorage.getItem("tempo-tactic-selected-pack-v1")) ??
      "hangingPiece-easy-01";
    const motif = selectedPackId.split("-")[0];
    await loadTacticsDeck(motif, selectedPackId.slice(motif.length + 1));
    return;
  }
  if (!usesLocalApi()) return;
  const paths: Partial<Record<View, string[]>> = {
    builder: ["repertoire/lines"],
    repertoire: ["repertoires"],
    games: ["games/summary", "repertoire/lines"],
    endgames: ["endgames/templates"],
    progress: ["progress"],
    settings: ["settings"],
  };
  await Promise.all(
    (paths[view] ?? []).map((path) =>
      readWorkspaceData(`${API_URL}/api/${path}`),
    ),
  );
}

export function preloadWorkspaces() {
  return Promise.allSettled(
    (
      [
        "tactics",
        "builder",
        "games",
        "repertoire",
        "endgames",
        "progress",
        "settings",
      ] as View[]
    ).map(preloadView),
  );
}
