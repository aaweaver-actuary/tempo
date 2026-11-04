import { test, expect } from "@playwright/test";
import { build } from "esbuild";

test("F10 orphan opening event holds authority and survives browser reload", async ({ page }) => {
  const bundle = await build({ entryPoints: ["app/lib/study-connection.ts"], bundle: true,
    format: "iife", globalName: "studyConnection", write: false, platform: "browser" });
  let requests = 0;
  await page.route("http://orphan.test/**", route => route.fulfill({ status: 200, contentType: "text/html", body: "<html></html>" }));
  await page.route("http://learning.test/**", route => { requests++; return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schema_version: 1, workspace_id: "workspace-a", timezone: "America/New_York", local_date: "2026-10-16", settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10, tactics_new_per_day: 5, include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 } }) }); });
  await page.goto("http://orphan.test/");
  const event = { attempt_id: "old-attempt", sequence: 1, decision_index: 0, decision_id: "d".repeat(64), expected_uci: "e2e4", kind: "first_response", observed_at: "2026-11-04T12:00:00Z", response_uci: "e2e4", assistance: null, disposition: "expected" };
  await page.evaluate(async event => {
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open("tempo-offline-training", 2);
      request.onupgradeneeded = () => {
        const db = request.result; db.createObjectStore("training");
        const attempts = db.createObjectStore("opening_attempts", { keyPath: "attempt_id" }); attempts.createIndex("delivery_state", "delivery_state");
        attempts.put({ attempt_id: "unrelated", delivery_state: "complete" });
        const events = db.createObjectStore("opening_events", { keyPath: ["attempt_id", "sequence"] }); events.createIndex("attempt_id", "attempt_id"); events.put(event);
      };
      request.onsuccess = () => { request.result.close(); resolve(); }; request.onerror = () => reject(request.error);
    });
  }, event);
  async function check() {
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    return page.evaluate(async () => {
      const client = (window as unknown as { studyConnection: { connectStudy: (origin: string, workspace: string, credential: string) => Promise<unknown> } }).studyConnection;
      let kind = "connected";
      try { await client.connectStudy("http://learning.test", "workspace-a", "unit-study-credential-" + "x".repeat(32)); }
      catch (error) { kind = (error as { kind: string }).kind; }
      const db = await new Promise<IDBDatabase>((resolve, reject) => { const r = indexedDB.open("tempo-offline-training"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
      try {
        const store = db.transaction("opening_events", "readonly").objectStore("opening_events");
        const keyRequest = store.getKey(["old-attempt", 1]); const valueRequest = store.get(["old-attempt", 1]);
        const read = <T>(request: IDBRequest<T>) => new Promise<T>((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
        const [key, value] = await Promise.all([read(keyRequest), read(valueRequest)]);
        return { kind, key, value, registry: localStorage.getItem("tempo-learning-authorities-v1"), session: sessionStorage.getItem("tempo-learning-connection-v1") };
      } finally { db.close(); }
    });
  }
  const expected = { kind: "authority", key: ["old-attempt", 1], value: event, registry: null, session: null };
  expect(await check()).toEqual(expected); expect(requests).toBe(0);
  await page.reload();
  expect(await check()).toEqual(expected); expect(requests).toBe(0);
});
