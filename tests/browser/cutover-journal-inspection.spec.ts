import { test, expect } from "@playwright/test";
import { build } from "esbuild";

test("F10 passive inspection retains original keys and values across browser reload", async ({ page }) => {
  const bundle = await build({ entryPoints: ["database/browser/cutover-journal-inspection.ts"], bundle: true,
    format: "iife", globalName: "journalInspection", write: false, platform: "browser" });
  await page.route("http://journal.test/**", route => route.fulfill({ status: 200, contentType: "text/html", body: "<html></html>" }));
  await page.goto("http://journal.test/");
  await page.evaluate(async () => {
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.open("original", 1);
      request.onupgradeneeded = () => {
        const intents = request.result.createObjectStore("intents", { keyPath: ["workspace", "key"] });
        intents.put({ workspace: "legacy", key: "pending", requestId: "original-request", state: "pending", payload: { attempt: "a", revision: 7 } });
        intents.put({ workspace: "legacy", key: "lost-ack", requestId: "lost-ack-request", state: "pending", data: { deliveryStarted: true } });
        intents.put({ workspace: "legacy", key: "committed", requestId: "committed-request", state: "committed", receipt: { id: 4 } });
        request.result.createObjectStore("training").put({ attempts: [{ attemptId: "offline-a", serverAcknowledged: false }] }, "prepared-daily-queue");
      };
      request.onsuccess = () => { request.result.close(); resolve(); }; request.onerror = () => reject(request.error);
    });
  });
  async function inspect(maxRecords: number) {
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    return page.evaluate(async (limit) => {
      const db = await new Promise<IDBDatabase>((resolve, reject) => { const r = indexedDB.open("original"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
      try { return await (window as unknown as { journalInspection: { inspectHeldJournal: (db: IDBDatabase, maxRecords: number) => Promise<{ records: unknown[] }> } }).journalInspection.inspectHeldJournal(db, limit); }
      finally { db.close(); }
    }, maxRecords);
  }
  const before = await inspect(4);
  expect(before.records).toEqual(expect.arrayContaining([
    expect.objectContaining({ store: "intents", primaryKey: ["legacy", "pending"], value: expect.objectContaining({ requestId: "original-request", payload: { attempt: "a", revision: 7 } }), disposition: "BLOCK" }),
    expect.objectContaining({ store: "intents", primaryKey: ["legacy", "lost-ack"], value: expect.objectContaining({ requestId: "lost-ack-request", state: "pending", data: { deliveryStarted: true } }) }),
    expect.objectContaining({ store: "intents", primaryKey: ["legacy", "committed"], value: expect.objectContaining({ requestId: "committed-request", receipt: { id: 4 } }) }),
    expect.objectContaining({ store: "training", primaryKey: "prepared-daily-queue", value: { attempts: [{ attemptId: "offline-a", serverAcknowledged: false }] } }),
  ]));
  expect(before.records).toHaveLength(4);
  await expect(inspect(3)).rejects.toThrow("incomplete: record budget exceeded");
  await page.reload();
  const after = await inspect(4);
  expect(after.records.map(record => JSON.stringify(record)).sort()).toEqual(before.records.map(record => JSON.stringify(record)).sort());
});

// Real browser persistence and production client composition; bootstrap HTTP is
// controlled transport only, not proof of multi-authority server authentication.
test("C02 C03 real browser retained authority survives reload and isolates malformed foreign work", async ({ page }) => {
  const bundle = await build({ stdin: { contents: `
    export * from "./app/lib/study-connection";
    export { setStudyPriorityScope, studyInteractionActive } from "./app/lib/study-priority";
  `, resolveDir: process.cwd() }, bundle: true, format: "iife", globalName: "authorityClient", write: false,
  platform: "browser", define: { __TEMPO_STUDY__: "true", __TEMPO_API_URL__: '""' } });
  const requests: string[] = [];
  let releaseForeground!: () => void;
  const foregroundGate = new Promise<void>(resolve => { releaseForeground = resolve; });
  const bootstrap = { schema_version: 1, workspace_id: "workspace", timezone: "UTC", local_date: "2026-11-12",
    settings: { study_only_mode: false, new_cards_per_day: 1, study_new_per_day: 1, tactics_new_per_day: 1,
      include_defensive_cards_in_daily_stack: false, light_first_interval_days: 1 } };
  await page.route("http://authority-shell.test/**", route => route.fulfill({ status: 200, contentType: "text/html", body: "<html></html>" }));
  await page.route(/http:\/\/authority-[abc]\.test\/.*/, async route => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" } });
      return;
    }
    requests.push(request.url());
    if (request.url().endsWith("/api/queue/window")) await foregroundGate;
    await route.fulfill({ status: 200, headers: { "Access-Control-Allow-Origin": "*" },
      json: request.url().endsWith("/api/study/bootstrap") ? bootstrap : { cards: [], count: 0 } });
  });
  await page.goto("http://authority-shell.test/");
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  type Client = typeof import("../../app/lib/study-connection") & {
    setStudyPriorityScope(value: boolean): void; studyInteractionActive(): boolean;
  };
  const credential = "browser-fixture-credential-not-secret";
  await page.evaluate(async credential => {
    const client = (window as unknown as { authorityClient: Client }).authorityClient;
    await client.connectStudy("http://authority-a.test", "workspace", credential);
    await client.connectStudy("http://authority-b.test", "workspace", credential);
    await client.connectStudy("http://authority-a.test", "workspace", credential);
    const name = `tempo-offline-training:${encodeURIComponent("http://authority-b.test:study:workspace")}`;
    await new Promise<void>((resolve, reject) => {
      const opened = indexedDB.open(name, 2);
      opened.onupgradeneeded = () => {
        opened.result.createObjectStore("training");
        opened.result.createObjectStore("opening_attempts", { keyPath: "attempt_id" });
        opened.result.createObjectStore("opening_events", { keyPath: "event_id" });
      };
      opened.onerror = () => reject(opened.error);
      opened.onsuccess = () => {
        const database = opened.result;
        const transaction = database.transaction(["training", "opening_attempts"], "readwrite");
        transaction.objectStore("training").put({ cards: [], original_marker: "missing-attempts-preserved" }, "original-malformed");
        transaction.objectStore("training").put({ attempts: [{ attemptId: "original-phone-id", serverAcknowledged: true,
          completedAt: "2026-11-01T02:03:04Z", answer: { moves: ["e2e4"] } }] }, "original-prepared");
        transaction.objectStore("opening_attempts").put({ attempt_id: "original-opening-id", delivery_state: "complete",
          request_id: "original-request-id", body: { revision: 7 } });
        transaction.oncomplete = () => { database.close(); resolve(); };
        transaction.onabort = () => { database.close(); reject(transaction.error); };
      };
    });
  }, credential);
  async function snapshot() {
    return page.evaluate(async () => {
      const client = (window as unknown as { authorityClient: Client }).authorityClient;
      const schemas = [];
      for (const item of (await indexedDB.databases()).sort((a, b) => a.name!.localeCompare(b.name!))) {
        const database = await new Promise<IDBDatabase>((resolve, reject) => {
          const request = indexedDB.open(item.name!); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
        });
        try {
          const stores = [];
          for (const name of [...database.objectStoreNames]) {
            const transaction = database.transaction(name, "readonly"), store = transaction.objectStore(name);
            const entries = await new Promise<unknown[]>((resolve, reject) => {
              const result: unknown[] = [], request = store.openCursor();
              request.onsuccess = () => { const cursor = request.result; if (!cursor) { resolve(result); return; }
                result.push({ key: cursor.primaryKey, value: cursor.value }); cursor.continue(); };
              request.onerror = () => reject(request.error);
            });
            stores.push({ name, keyPath: store.keyPath, indexes: [...store.indexNames], entries });
          }
          schemas.push({ name: database.name, version: database.version, stores });
        } finally { database.close(); }
      }
      return { schemas: JSON.stringify(schemas), exported: JSON.stringify(await client.retainedStudyData()),
        registry: localStorage.getItem("tempo-learning-authorities-v1") };
    });
  }
  const before = await snapshot();
  await page.reload();
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.evaluate(async credential => {
    const client = (window as unknown as { authorityClient: Client }).authorityClient;
    await client.connectStudy("http://authority-a.test", "workspace", credential);
    // Both original-authority resume and explicitly registered recovery retain B's work.
    await client.assertStudyAuthority("http://authority-b.test", "workspace", "recover");
    await client.assertStudyAuthority("http://authority-b.test", "workspace", "cutover");
  }, credential);
  expect(await snapshot()).toEqual(before);
  // Recognized records are terminal: only the malformed foreign training record
  // can explain this new-authority hold, not an unrelated pending record.
  const beforeCutover = requests.length;
  const held = await page.evaluate(async credential => {
    const client = (window as unknown as { authorityClient: Client }).authorityClient;
    try { await client.connectStudy("http://authority-c.test", "workspace", credential); return "unexpected success"; }
    catch (error) { return (error as { kind: string }).kind; }
  }, credential);
  expect(held).toBe("authority");
  expect(requests).toHaveLength(beforeCutover);
  expect(await snapshot()).toEqual(before);
  // The real compatibility composition acquires its existing Study activity hold.
  await page.evaluate(() => {
    const client = (window as unknown as { authorityClient: Client }).authorityClient;
    client.setStudyPriorityScope(true);
    (window as unknown as { foreground: Promise<Response> }).foreground = client.learningFetch("/api/queue/window");
  });
  await expect.poll(() => requests.some(url => url.endsWith("/api/queue/window"))).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as unknown as { authorityClient: Client }).authorityClient.studyInteractionActive())).toBe(true);
  releaseForeground();
  await page.evaluate(async () => { await (window as unknown as { foreground: Promise<Response> }).foreground; });
  // Existing quiet period is three seconds after release, not an immediate no-op.
  await expect.poll(() => page.evaluate(() => (window as unknown as { authorityClient: Client }).authorityClient.studyInteractionActive()), { timeout: 5000 }).toBe(false);
  expect(await snapshot()).toEqual(before);
});
