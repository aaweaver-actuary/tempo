import type { APIRequestContext } from "@playwright/test";

type FixtureRepertoire = {
  id: string;
  graph_state?: string;
  graph_error?: string | null;
  integrity_status?: string;
  integrity_scan_status?: string;
  integrity_scan_error?: string | null;
};
type FixtureQueue = {
  cards: { id: string; repertoire_id: string }[];
  projection?: { state: string; generation: number; refresh_pending: number | boolean; last_error?: string | null };
};

export async function readDeletionFixturePublication(request: APIRequestContext, api: string, repertoireIds: string[]) {
  const options = { headers: { "X-Tempo-Work-Class": "background" } };
  const [repertoireResponse, queueResponse] = await Promise.all([
    request.get(`${api}/repertoires`, options), request.get(`${api}/queue/today`, options),
  ]);
  for (const [label, response] of [["repertoires", repertoireResponse], ["queue", queueResponse]] as const) {
    if (!response.ok()) throw new Error(`Deletion fixture ${label}: HTTP ${response.status()} ${await response.text()}`);
  }
  const allRepertoires = (await repertoireResponse.json()).repertoires as FixtureRepertoire[];
  const queue = await queueResponse.json() as FixtureQueue;
  if (!Array.isArray(allRepertoires) || !Array.isArray(queue.cards)) throw new Error("Deletion fixture publication response is malformed");
  const repertoires = allRepertoires.filter(repertoire => repertoireIds.includes(repertoire.id));
  for (const repertoire of repertoires) {
    if (repertoire.graph_state === "failed" || repertoire.integrity_scan_status === "failed") {
      throw new Error(`Deletion fixture repertoire ${repertoire.id} publication failed: ${repertoire.graph_error ?? repertoire.integrity_scan_error ?? "no error detail"}`);
    }
  }
  if (queue.projection?.state === "failed") throw new Error(`Deletion fixture queue publication failed: ${queue.projection.last_error ?? "no error detail"}`);
  const ready = repertoireIds.every(repertoireId => {
    const repertoire = repertoires.find(item => item.id === repertoireId);
    return repertoire?.graph_state === "ready" && repertoire.integrity_status === "clean"
      && repertoire.integrity_scan_status === "idle"
      && queue.cards.some(card => card.repertoire_id === repertoireId);
  }) && queue.projection?.state === "ready" && !queue.projection.refresh_pending;
  return { ready, queue, repertoires };
}
