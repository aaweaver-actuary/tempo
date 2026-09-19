import { API_URL, assetUrl } from "../const";
import type { PackagedPuzzle, PracticeCard, View } from "../types";
import { usesLocalApi } from "../utils/local";
import { runStudyTask } from "./background-study";
import * as z from "zod";
import { tacticProgressSchema } from "../domain/schemas";
import { parseData } from "./validated-data";

const requests = new Map<
  string,
  { createdAt: number; promise: Promise<unknown> }
>();
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
  if (cached && Date.now() - cached.createdAt < 30_000)
    return cached.promise.then((raw) =>
      schema ? parseData(schema, raw, url) : raw,
    );
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
    .catch((error) => {
      requests.delete(url);
      throw error;
    })
    .finally(() => clearTimeout(requestTimeout));
  requests.set(url, { createdAt: Date.now(), promise });
  return promise.then((raw) => (schema ? parseData(schema, raw, url) : raw));
}

export function invalidateWorkspaceData() {
  requests.clear();
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
    const progress = usesLocalApi()
      ? await readWorkspaceData(
          `${API_URL}/api/tactics/progress`,
          tacticProgressSchema,
        )
      : {};
    const stage =
      ["easy", "medium", "hard", "focused"].find(
        (item) =>
          (progress[`hangingPiece:${item}`]?.clean ?? 0) <
          (item === "focused" ? 250 : 100),
      ) ?? "focused";
    await loadTacticsDeck("hangingPiece", stage);
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
