import { expect, it } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { inspectHeldJournal } from "../../database/browser/cutover-journal-inspection";

it("holds pending, committed and offline originals with inline and out-of-line primary keys", async () => {
  const factory = new IDBFactory();
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = factory.open("original", 1);
    request.onupgradeneeded = () => {
      const intents = request.result.createObjectStore("intents", { keyPath: ["workspace", "key"] });
      intents.put({ workspace: "old:study", key: "review:1", requestId: "request-1", state: "pending", payload: { revision: 7 } });
      intents.put({ workspace: "old:study", key: "review:lost-ack", requestId: "request-lost-ack", state: "pending", data: { deliveryStarted: true } });
      intents.put({ workspace: "old:study", key: "review:2", requestId: "request-2", state: "committed", receipt: { id: 4 } });
      request.result.createObjectStore("training").put({ attempts: [{ attemptId: "a", serverAcknowledged: false }] }, "prepared-daily-queue");
    };
    request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
  });
  const before = await inspectHeldJournal(database, 4);
  expect(before.records.map(({ store, primaryKey }) => JSON.stringify([store, primaryKey])).sort()).toEqual([
    JSON.stringify(["intents", ["old:study", "review:1"]]), JSON.stringify(["intents", ["old:study", "review:2"]]), JSON.stringify(["intents", ["old:study", "review:lost-ack"]]), JSON.stringify(["training", "prepared-daily-queue"]),
  ]);
  expect(before.records.every(record => record.disposition === "BLOCK" && record.reason === "unconfirmed-authority")).toBe(true);
  await expect(inspectHeldJournal(database, 3)).rejects.toThrow("incomplete: record budget exceeded");
  await expect(inspectHeldJournal(database, 0)).rejects.toThrow("positive safe integer");
  database.close();
  const reopened = await new Promise<IDBDatabase>((resolve, reject) => { const request = factory.open("original"); request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
  const after = await inspectHeldJournal(reopened, 4);
  expect(after.records.map(record => JSON.stringify(record)).sort()).toEqual(before.records.map(record => JSON.stringify(record)).sort());
  reopened.close();
});
