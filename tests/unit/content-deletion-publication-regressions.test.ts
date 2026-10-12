// @vitest-environment node
import type { APIRequestContext } from "@playwright/test";
import { expect, it, vi } from "vitest";
import { readDeletionFixturePublication } from "../browser/content-deletion-publication";

const api = "http://fixture/api";
const repertoireIds = ["individual-delete", "next-delete"];
function publicationFixture() {
  return {
    repertoires: repertoireIds.map(id => ({ id, graph_state: "ready", integrity_status: "clean", integrity_scan_status: "idle" })),
    queue: { cards: repertoireIds.map(repertoire_id => ({ id: `${repertoire_id}-card`, repertoire_id })),
      projection: { state: "ready", generation: 6, refresh_pending: 0 } },
  };
}
function publicationRequest(fixture = publicationFixture()) {
  const get = vi.fn(async (url: string, options?: { headers?: Record<string, string> }) => ({
    ok: (): boolean => true, status: (): number => 200, text: async () => "",
    json: async () => url.endsWith("/repertoires") ? { repertoires: fixture.repertoires }
      : options?.headers?.["X-Tempo-Work-Class"] === "background" ? fixture.queue
      : { ...fixture.queue, projection: { ...fixture.queue.projection, state: "refreshing", refresh_pending: 1 } },
  }));
  return { request: { get } as unknown as APIRequestContext, get };
}

it("B00 deletion preparation observers use background admission while publication completes", async () => {
  const { request, get } = publicationRequest();
  const observed = await readDeletionFixturePublication(request, api, repertoireIds);
  expect(observed.ready).toBe(true);
  expect(observed.queue.projection?.generation).toBe(6);
  expect(observed.repertoires.map(repertoire => repertoire.id)).toEqual(repertoireIds);
  expect(get).toHaveBeenCalledWith(`${api}/repertoires`, { headers: { "X-Tempo-Work-Class": "background" } });
  expect(get).toHaveBeenCalledWith(`${api}/queue/today`, { headers: { "X-Tempo-Work-Class": "background" } });
});

it.each(["graph refreshing", "integrity running", "integrity unchecked", "missing repertoire", "pending queue", "refreshing queue", "unrelated cards"])(
  "B00 deletion preparation rejects incomplete publication: %s", async incompleteState => {
    const fixture = publicationFixture();
    if (incompleteState === "graph refreshing") fixture.repertoires[1].graph_state = "refreshing";
    if (incompleteState === "integrity running") fixture.repertoires[1].integrity_scan_status = "running";
    if (incompleteState === "integrity unchecked") fixture.repertoires[1].integrity_status = "unchecked";
    if (incompleteState === "missing repertoire") fixture.repertoires.pop();
    if (incompleteState === "pending queue") fixture.queue.projection.refresh_pending = 1;
    if (incompleteState === "refreshing queue") fixture.queue.projection.state = "refreshing";
    if (incompleteState === "unrelated cards") fixture.queue.cards[1].repertoire_id = "unrelated";
    const { request } = publicationRequest(fixture);
    expect((await readDeletionFixturePublication(request, api, repertoireIds)).ready).toBe(false);
  },
);

it.each(["/repertoires", "/queue/today"])("B00 deletion preparation rejects HTTP failures: %s", async endpoint => {
  const { request, get } = publicationRequest();
  const originalGet = get.getMockImplementation()!;
  get.mockImplementation(async (url, options) => url.endsWith(endpoint)
    ? { ok: () => false, status: () => 503, text: async () => "Publication unavailable", json: async () => { throw new Error("Must reject HTTP failure before parsing"); } }
    : originalGet(url, options));
  await expect(readDeletionFixturePublication(request, api, repertoireIds)).rejects.toThrow("HTTP 503");
});

it.each(["graph", "integrity", "queue"])("B00 deletion preparation rejects failed publication: %s", async failedState => {
  const fixture = publicationFixture();
  if (failedState === "graph") fixture.repertoires[1].graph_state = "failed";
  if (failedState === "integrity") fixture.repertoires[1].integrity_scan_status = "failed";
  if (failedState === "queue") fixture.queue.projection.state = "failed";
  const { request } = publicationRequest(fixture);
  await expect(readDeletionFixturePublication(request, api, repertoireIds)).rejects.toThrow(/failed/);
});
