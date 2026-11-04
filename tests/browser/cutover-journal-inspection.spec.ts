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
