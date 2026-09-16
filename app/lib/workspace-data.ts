import { API_URL, assetUrl } from "../const";
import type { PackagedPuzzle, PracticeCard, View } from "../types";
import { usesLocalApi } from "../utils/local";
import { runStudyTask } from "./background-study";

const requests = new Map<string, { createdAt: number; promise: Promise<unknown> }>();
const decks = new Map<string, Promise<Array<{ record: PackagedPuzzle; card: PracticeCard }>>>();

export function readWorkspaceData<T>(url: string): Promise<T> {
  const cached = requests.get(url);
  if (cached && Date.now() - cached.createdAt < 30_000) return cached.promise as Promise<T>;
  const promise = fetch(url).then(response => {
    if (!response.ok) throw new Error(`Could not load ${new URL(url, document.baseURI).pathname} (HTTP ${response.status}).`);
    return response.json() as Promise<T>;
  }).catch(error => { requests.delete(url); throw error; });
  requests.set(url, { createdAt: Date.now(), promise });
  return promise;
}

export function invalidateWorkspaceData() { requests.clear(); }
export async function readWorkspaceResponse(url: string) { return Response.json(await readWorkspaceData(url)); }
export function resetWorkspaceCache() { requests.clear(); decks.clear(); }

export function loadTacticsDeck(motif: string, stage: string) {
  const deckId = `${motif}-${stage}`;
  let deck = decks.get(deckId);
  if (!deck) {
    deck = readWorkspaceData<PackagedPuzzle[]>(assetUrl("data/tactics-decks.json"))
      .then(records => runStudyTask<Array<{ record: PackagedPuzzle; card: PracticeCard }>>({ kind: "deck", records, deckId }))
      .catch(error => { decks.delete(deckId); throw error; });
    decks.set(deckId, deck);
  }
  return deck;
}

export async function preloadView(view: View) {
  if (view === "tactics") {
    const progress = usesLocalApi() ? await readWorkspaceData<Record<string, { clean: number }>>(`${API_URL}/api/tactics/progress`) : {};
    const stage = ["easy", "medium", "hard", "focused"].find(item => (progress[`hangingPiece:${item}`]?.clean ?? 0) < (item === "focused" ? 250 : 100)) ?? "focused";
    await loadTacticsDeck("hangingPiece", stage);
    return;
  }
  if (!usesLocalApi()) return;
  const paths: Partial<Record<View, string[]>> = {
    builder: ["repertoire/lines"], repertoire: ["repertoires"], games: ["games/summary", "repertoire/lines"],
    endgames: ["endgames/templates"], progress: ["progress"], settings: ["settings"],
  };
  await Promise.all((paths[view] ?? []).map(path => readWorkspaceData(`${API_URL}/api/${path}`)));
}

export function preloadWorkspaces() {
  return Promise.allSettled((["tactics", "builder", "games", "repertoire", "endgames", "progress", "settings"] as View[]).map(preloadView));
}
