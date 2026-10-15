import { readFileSync } from "node:fs";
import type { Page, BrowserContext } from "@playwright/test";
import { test as observedTest, expect } from "../browser/observability";
import { ControlledApi, finishControlledContext } from "./strict-api";
import examples from "../fixtures/controlled-browser/startup.json" with { type: "json" };

export { expect };
const controlledApis = new WeakMap<BrowserContext, ControlledApi>();
export const test = observedTest.extend<{ controlledTraffic: void }>({
  controlledTraffic: [async ({ context, baseURL }, runTest, testInfo) => {
    if (!baseURL) throw new Error("Missing controlled origin");
    const api = new ControlledApi(baseURL, new Set(JSON.parse(readFileSync(process.env.TEMPO_CONTROLLED_ASSET_MANIFEST!, "utf8"))));
    controlledApis.set(context, api);
    await api.install(context);
    try { await runTest(); }
    finally {
      try { await finishControlledContext(context, api); }
      finally {
        await testInfo.attach("controlled-api-traffic", { body: JSON.stringify({ traffic: api.traffic, failures: api.failures }, null, 2), contentType: "application/json" });
      }
    }
  }, { auto: true }],
});
export const startFen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
export function openingCard(id: string, title: string, firstMove: string, queueEntry: number) {
  return { id, queue_entry_id: queueEntry, start_fen: startFen, moves: [firstMove],
    content_type: "opening" as const, repertoire_name: title, repertoire_source: "White.pgn",
    first_correct_at: "2026-09-17T12:00:00Z", trained_color: "white" as const };
}
type ControlledQueueCard = { id: string; queue_entry_id: number; start_fen: string; moves: string[]; content_type: string; repertoire_name: string; repertoire_source: string; first_correct_at: string | null; trained_color: string | null };
export async function prepareControlledUI(page: Page, trainingCards: ControlledQueueCard[] = examples["/api/queue/window"].cards, navigate = true, acknowledgeLegacy = true) {
  const api = controlledApis.get(page.context());
  if (!api) throw new Error("Controlled fixture must be installed before navigation");
  await page.clock.setFixedTime(new Date("2026-09-18T16:00:00Z"));
  await page.addInitScript((acknowledge) => {
    if (acknowledge) localStorage.setItem("tempo-study-intents-cutover-v1", "acknowledged");
    let seed = 42;
    Math.random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
    for (const key of ["tempo-sound", "tempo-stockfish-on", "tempo-maia-on", "tempo-games-engine-on", "tempo-explorer-on"]) localStorage.setItem(key, "false");
  }, acknowledgeLegacy);
  for (const [path, json] of Object.entries(examples)) {
    if (path === "/api/queue/window" || path === "/api/system/activity" || path === "/api/discoveries") continue;
    api.register({ method: "GET", path, query: {} }, route => route.fulfill({ json }));
  }
  for (const query of ([{ limit: "20" }, { limit: "20", include_opening_evidence: "true" }] as Record<string, string>[]))
    api.register({ method: "GET", path: "/api/queue/window", query }, route => route.fulfill({ json: { cards: trainingCards, count: trainingCards.length } }));
  api.register({ method: "POST", path: "/api/system/browser-activity", query: {}, validate: request => {
    if (request.body !== null) throw new Error("Browser activity must have no body");
  } }, route => route.fulfill({ json: { active: true } }));
  api.register({ method: "GET", path: "/api/system/activity", query: { offset: "0", limit: "50" } }, route => route.fulfill({ json: examples["/api/system/activity"] }));
  api.register({ method: "GET", path: "/api/discoveries", query: { offset: "0", limit: "100" } }, route => route.fulfill({ json: examples["/api/discoveries"] }));
  for (const card of trainingCards) api.register({ method: "GET", path: `/api/cards/${card.id}/teaching`, query: {} }, route => route.fulfill({ json: { states: [] } }));
  if (navigate) await page.goto("/");
  return api;
}
