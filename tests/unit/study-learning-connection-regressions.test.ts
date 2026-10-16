import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { openIntentStore } from "@tempo/browser-intents";

const bootstrap = { schema_version: 1, workspace_id: "workspace-a", timezone: "America/New_York", local_date: "2026-10-16",
  settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10, tactics_new_per_day: 5,
    include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 } };
const credential = "unit-study-credential-" + "x".repeat(32);
beforeEach(() => {
  vi.resetModules(); vi.stubGlobal("__TEMPO_STUDY__", true);
  vi.stubGlobal("indexedDB", new IDBFactory()); localStorage.clear(); sessionStorage.clear();
});
afterEach(() => { vi.unstubAllGlobals(); });
async function connected() {
  const client = await import("../../app/lib/study-connection");
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(bootstrap)));
  vi.stubGlobal("fetch", fetcher);
  await client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  return { client, fetcher };
}

it("F2 connection authenticates bootstrap without issuing a queue or persisting credentials in commands", async () => {
  const { client, fetcher } = await connected();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0][0])).toBe("http://learning.test:8001/api/study/bootstrap");
  const options = fetcher.mock.calls[0][1];
  expect(options.headers.get("Authorization")).toBe(`Bearer ${credential}`);
  expect(options.headers.get("X-Tempo-Workspace")).toBe("workspace-a");
  expect(options.redirect).toBe("error");
  expect(client.studyWorkspace()).toBe("http://learning.test:8001:study:workspace-a");
  expect(JSON.stringify(await client.retainedStudyData())).not.toContain(credential);
  client.disconnectStudy(); expect(sessionStorage.getItem("tempo-learning-connection-v1")).toBeNull();
});

it("F2 delivery preserves the frozen request while injecting current authentication", async () => {
  const { client, fetcher } = await connected();
  const body = '{"attempt_id":"original","recorded_at":"2026-10-15T12:00:00Z"}';
  const headers = { "Idempotency-Key": "original-key", "X-Tempo-Work-Class": "background" };
  await client.learningFetch("http://learning.test:8001/api/cards/card-a/review", { method: "POST", headers, body });
  const options = fetcher.mock.calls.at(-1)![1];
  expect(options.body).toBe(body); expect(options.headers.get("Idempotency-Key")).toBe("original-key");
  expect(options.headers.has("X-Tempo-Work-Class")).toBe(false);
  expect(headers).toEqual({ "Idempotency-Key": "original-key", "X-Tempo-Work-Class": "background" });
});

it("F2 Request delivery preserves method body signal and idempotency identity", async () => {
  const { client, fetcher } = await connected();
  const original = new Request("http://learning.test:8001/api/cards/card-a/review", {
    method: "POST", body: '{"attempt_id":"frozen"}', headers: { "Idempotency-Key": "frozen-key" },
  });
  await client.learningFetch(original);
  const delivered = fetcher.mock.calls.at(-1)![0] as Request;
  expect(delivered.method).toBe("POST"); expect(await delivered.text()).toBe('{"attempt_id":"frozen"}');
  expect(delivered.headers.get("Idempotency-Key")).toBe("frozen-key");
  expect(delivered.headers.get("Authorization")).toBe(`Bearer ${credential}`);
  expect(delivered.redirect).toBe("error");
});

it.each(["http://other.test/api/cards/card-a/review", "http://learning.test:8001/api/games/sync", "http://learning.test:8001/api/settings"])(
  "F2 never delivers Study credentials to another authority or optional capability: %s", async endpoint => {
    const { client, fetcher } = await connected();
    await expect(client.learningFetch(endpoint)).rejects.toThrow("unavailable");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

it("F2 pending old-origin envelopes block connection without rewriting original authority", async () => {
  const retained = await openIntentStore({ workspace: "http://old.test:study" });
  const envelope = { key: "review:original", kind: "review", attemptId: "original", requestId: "review-attempt:original",
    endpoint: "/api/cards/original/review", createdAt: "2026-10-15T12:00:00Z", payload: { outcome: "correct" } };
  await retained.put(envelope); retained.close();
  const { assertStudyAuthority, retainedStudyData } = await import("../../app/lib/study-connection");
  await expect(assertStudyAuthority("http://new.test", "workspace-a")).rejects.toThrow("original service");
  const records = (await retainedStudyData()).databases["tempo-study-intents"].intents as {endpoint:string;workspace:string}[];
  expect(records[0].endpoint).toBe(envelope.endpoint); expect(records[0].workspace).toBe("http://old.test:study");
});

it("F2 retained terminal work is inspectable and does not resurrect or block another authority", async () => {
  const retained = await openIntentStore({ workspace: "http://old.test:study" });
  await retained.put({ key: "review:done", kind: "review", attemptId: "done", requestId: "done", endpoint: "/original", createdAt: "original-time", payload: {} });
  await retained.update("review:done", current => ({ ...current, state: "committed", receipt: { persisted: true } }));
  retained.close();
  const { assertStudyAuthority } = await import("../../app/lib/study-connection");
  const result = await assertStudyAuthority("http://new.test", "workspace-a");
  expect(result.databases["tempo-study-intents"].intents).toHaveLength(1);
});

it("F2 unscoped legacy data is retained and blocks automatic conversion", async () => {
  localStorage.setItem("tempo-pending-training-reviews-v1", '[{"attemptId":"legacy"}]');
  const { assertStudyAuthority } = await import("../../app/lib/study-connection");
  await expect(assertStudyAuthority("http://new.test", "workspace-a")).rejects.toThrow("unconfirmed authority");
  expect(localStorage.getItem("tempo-pending-training-reviews-v1")).toBe('[{"attemptId":"legacy"}]');
});

it("F2 database inspection aborts remain actionable failures before delivery", async () => {
  vi.stubGlobal("indexedDB", { open: () => { throw new DOMException("Denied", "SecurityError"); } });
  const { connectStudy } = await import("../../app/lib/study-connection");
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(connectStudy("http://learning.test", "workspace-a", credential)).rejects.toThrow("Denied");
  expect(fetcher).not.toHaveBeenCalled();
});

it("F2 unsupported bootstrap and mismatched workspace never authorize Study", async () => {
  const client = await import("../../app/lib/study-connection");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ ...bootstrap, workspace_id: "another" })));
  await expect(client.connectStudy("http://learning.test", "workspace-a", credential)).rejects.toThrow("another workspace");
  expect(client.studyConnection()).toBeUndefined();
});

it("F2 Study dates follow Learning timezone across the browser's midnight", async () => {
  const { client } = await connected();
  expect(client.learningDayKey(new Date("2026-10-16T02:00:00Z"))).toBe("2026-10-15");
});

async function legacyPreparedQueue(attempts: unknown[]) {
  return new Promise<void>((resolve, reject) => {
    const opening = indexedDB.open("tempo-offline-training", 2);
    opening.onupgradeneeded = () => opening.result.createObjectStore("training");
    opening.onerror = () => reject(opening.error);
    opening.onsuccess = () => {
      const database = opening.result;
      const transaction = database.transaction("training", "readwrite");
      transaction.objectStore("training").put({ localDate: "2026-10-16", cards: [{ id: "original-card" }], attempts }, "prepared-daily-queue");
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onabort = () => { database.close(); reject(transaction.error); };
    };
  });
}

it("F2 pending legacy phone work blocks a new authority and stays inspectable", async () => {
  await legacyPreparedQueue([{ cardId: "original-card", outcome: "again", completedAt: "original-time" }]);
  const client = await import("../../app/lib/study-connection");
  await expect(client.assertStudyAuthority("http://new.test", "workspace-a")).rejects.toThrow("original service");
  const retained = await client.retainedStudyData();
  expect(retained.databases["tempo-offline-training"].training).toEqual([
    { localDate: "2026-10-16", cards: [{ id: "original-card" }], attempts: [{ cardId: "original-card", outcome: "again", completedAt: "original-time" }] }]);
});

it("F2 a new authority cannot read a legacy cached queue even when old reviews are settled", async () => {
  await legacyPreparedQueue([{ cardId: "original-card", serverReviewId: 7 }]);
  const { client } = await connected();
  const { readPreparedTraining } = await import("../../app/lib/offline-training");
  expect(await readPreparedTraining()).toBeNull();
  expect(client.studyOfflineDatabaseName()).not.toBe("tempo-offline-training");
  expect((await client.retainedStudyData()).databases["tempo-offline-training"].training).toHaveLength(1);
});

it("F2 malformed authority metadata blocks connection without replacing the retained registry", async () => {
  localStorage.setItem("tempo-learning-authorities-v1", "incompatible-original");
  const client = await import("../../app/lib/study-connection");
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(client.connectStudy("http://new.test", "workspace-a", credential)).rejects.toThrow();
  expect(localStorage.getItem("tempo-learning-authorities-v1")).toBe("incompatible-original");
  expect(fetcher).not.toHaveBeenCalled();
});

it("F2 bootstrap never changes the active authority and rechecks an older writer before accepting a switch", async () => {
  const { client, fetcher } = await connected();
  const bootstrapReply = Promise.withResolvers<Response>();
  fetcher.mockImplementationOnce(() => bootstrapReply.promise);
  const switching = client.connectStudy("http://new-learning.test", "workspace-a", credential);
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(client.LEARNING_URL).toBe("http://learning.test:8001");
  const prior = await openIntentStore({ workspace: client.studyWorkspace() });
  await prior.put({ key: "review:older-writer", kind: "review", attemptId: "older-writer", requestId: "original",
    endpoint: "/api/cards/original/review", createdAt: "original-time", payload: { outcome: "again" } });
  prior.close();
  bootstrapReply.resolve(Response.json(bootstrap));
  await expect(switching).rejects.toThrow("original service");
  expect(client.studyConnection()?.origin).toBe("http://learning.test:8001");
  expect(JSON.parse(sessionStorage.getItem("tempo-learning-connection-v1")!).origin).toBe("http://learning.test:8001");
});
