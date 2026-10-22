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
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('B independent activity authenticates without changing authority or prompting recovery on rejection', async () => {
  const {client,fetcher}=await connected();
  const original=client.studyConnection();
  const authentication=vi.fn();window.addEventListener('tempo:study-auth-required',authentication);
  try {
    fetcher.mockResolvedValueOnce(new Response('',{status:401}));
    await expect(client.sendStudyActivity('tab-a')).rejects.toThrow('HTTP 401');
    expect(authentication).not.toHaveBeenCalled();expect(client.studyConnection()).toEqual(original);
    const [address,options]=fetcher.mock.calls.at(-1)!;
    expect(String(address)).toBe('http://learning.test:8001/api/study/activity?session_id=tab-a&expires_in_ms=3000');
    expect(options.headers.get('Authorization')).toBe(`Bearer ${credential}`);
    expect(options.headers.get('X-Tempo-Workspace')).toBe('workspace-a');
    expect(options.method).toBe('POST');expect(options.credentials).toBe('omit');
  } finally {window.removeEventListener('tempo:study-auth-required',authentication);}
});

it.each(['resolve','reject','throw'])('B essential request hold covers delayed %s and always releases into the quiet period',async outcome=>{
  const {client,fetcher}=await connected();
  vi.useFakeTimers();
  const priority=await import('../../app/lib/study-priority');priority.setStudyPriorityScope(true);
  let complete:(value:Response)=>void=()=>{},fail:(error:Error)=>void=()=>{};
  fetcher.mockImplementationOnce(()=>outcome==='throw'?(()=>{throw new Error('synchronous transport failure');})():
    new Promise<Response>((resolve,reject)=>{complete=resolve;fail=reject;}));
  const pending=client.learningFetch('http://learning.test:8001/api/cards/card-a/review',{method:'POST'}).catch(error=>error);
  if(outcome!=='throw') {
    await vi.advanceTimersByTimeAsync(5000);expect(priority.studyInteractionActive()).toBe(true);
    if(outcome==='resolve')complete(Response.json({persisted:true}));else fail(new Error('transport failure'));
  }
  await pending;
  expect(priority.studyInteractionActive()).toBe(true);
  await vi.advanceTimersByTimeAsync(3000);expect(priority.studyInteractionActive()).toBe(false);
});

async function retainAuthority(origin: string, requestId: string) {
  const workspace = `${origin}:study:workspace-a`;
  const store = await openIntentStore({ workspace });
  await store.put({ key: `review:${requestId}`, kind: "review", attemptId: requestId, requestId,
    endpoint: `${origin}/api/cards/original/review`, createdAt: "original-time", payload: { outcome: "correct" } });
  store.close();
}

it.each(["a", "b"])("pr291 R2 original authority %s resumes with A and B pending without rewriting foreign data", async selected => {
  const { client } = await connected();
  await client.connectStudy("http://b.test", "workspace-a", credential);
  await retainAuthority("http://learning.test:8001", "a");
  await retainAuthority("http://b.test", "b");
  const before = await client.retainedStudyData();
  // A reload restores each tab's own session connection.
  const origin = selected === "a" ? "http://learning.test:8001" : "http://b.test";
  sessionStorage.setItem("tempo-learning-connection-v1", JSON.stringify({ origin, workspace: "workspace-a", credential, bootstrap }));
  vi.resetModules();
  const restored = await import("../../app/lib/study-connection");
  await restored.connectStudy(origin, "workspace-a", credential);
  expect(await restored.retainedStudyData()).toEqual(before);
});

it.each(["http://learning.test:8001", "http://b.test"])("pr291 R2 explicit registered recovery of %s works without session storage", async origin => {
  const { client } = await connected();
  await client.connectStudy("http://b.test", "workspace-a", credential);
  await retainAuthority("http://learning.test:8001", "a"); await retainAuthority("http://b.test", "b");
  client.disconnectStudy();
  await client.connectStudy(origin, "workspace-a", credential);
  expect(client.studyConnection()?.origin).toBe(origin);
});

it.each(["tempo-pending-tactic-capture-v1", "tempo-pending-card-delete-v1"])("pr291 R3 inventories and preserves ambiguous %s before connecting", async key => {
  const original = '{"body":"original-immutable-command"}'; localStorage.setItem(key, original);
  const client = await import("../../app/lib/study-connection");
  expect((await client.retainedStudyData()).legacy[key]).toBe(original);
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  await expect(client.connectStudy("http://new.test", "workspace-a", credential)).rejects.toThrow("authority");
  expect(localStorage.getItem(key)).toBe(original); expect(fetcher).not.toHaveBeenCalled();
});
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

it.each(["http://new.test", "http://b.test"])("pr291 R2 active A cannot switch to %s while its original command is pending", async origin => {
  const { client, fetcher } = await connected(); await client.connectStudy("http://b.test", "workspace-a", credential); await client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  await retainAuthority(client.studyConnection()!.origin, "a"); const before = await client.retainedStudyData(); const requests = fetcher.mock.calls.length;
  await expect(client.connectStudy(origin, "workspace-a", credential)).rejects.toMatchObject({ kind: "authority" });
  expect(await client.retainedStudyData()).toEqual(before); expect(fetcher).toHaveBeenCalledTimes(requests);
});
it("pr291 R2 workspace changes remain authority changes", async () => {
  const { client, fetcher } = await connected(); await retainAuthority(client.studyConnection()!.origin, "a");
  const retainedBefore = await client.retainedStudyData();
  await expect(client.connectStudy("http://learning.test:8001", "workspace-b", credential)).rejects.toMatchObject({ kind: "authority" });
  expect(fetcher).toHaveBeenCalledTimes(1); expect(await client.retainedStudyData()).toEqual(retainedBefore);
});
it.each([
  { label: "authority registry", storage: localStorage, key: "tempo-learning-authorities-v1" },
  { label: "session connection", storage: sessionStorage, key: "tempo-learning-connection-v1" },
])("pr376 failed $label write blocks the new Learning connection", async ({ storage, key }) => {
  const { client, fetcher } = await connected(); const previous = client.studyConnection();
  const previousSession = sessionStorage.getItem("tempo-learning-connection-v1");
  const retainedBefore = await client.retainedStudyData();
  const originalSetItem = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, name: string, value: string) {
    if (this === storage && name === key) throw new DOMException("Storage write denied", "QuotaExceededError");
    return originalSetItem.call(this, name, value);
  });

  await expect(client.connectStudy("http://candidate.test", "workspace-a", credential))
    .rejects.toMatchObject({ kind: "storage", cachedPracticeAvailable: false });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(client.studyConnection()).toEqual(previous);
  expect(sessionStorage.getItem("tempo-learning-connection-v1")).toBe(previousSession);
  expect(await client.retainedStudyData()).toEqual(retainedBefore);
});
it.each([false, true])("pr291 R2 original resumption permits registered B work arriving during bootstrap (A pending=%s)", async pendingA => {
  const { client, fetcher } = await connected(); await client.connectStudy("http://b.test", "workspace-a", credential); await client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  if (pendingA) await retainAuthority("http://learning.test:8001", "a");
  const reply = Promise.withResolvers<Response>(); fetcher.mockImplementationOnce(() => reply.promise);
  const resuming = client.connectStudy("http://learning.test:8001", "workspace-a", credential); await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(4));
  await retainAuthority("http://b.test", "b"); const before = await client.retainedStudyData(); reply.resolve(Response.json(bootstrap));
  await resuming; expect(await client.retainedStudyData()).toEqual(before);
});
it.each([401, 403])("pr291 R2 original recovery still rejects HTTP %s without touching foreign commands", async status => {
  const { client, fetcher } = await connected(); await client.connectStudy("http://b.test", "workspace-a", credential); client.disconnectStudy();
  await retainAuthority("http://learning.test:8001", "a"); await retainAuthority("http://b.test", "b"); const before = await client.retainedStudyData();
  fetcher.mockResolvedValueOnce(new Response(null, { status })); await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential)).rejects.toMatchObject({ kind: "http", status });
  expect(client.studyConnection()).toBeUndefined(); expect(await client.retainedStudyData()).toEqual(before);
});
it("pr291 R2 mismatched registry bindings fail closed without rewriting the registry", async () => {
  const { client, fetcher } = await connected(); const registry = JSON.parse(localStorage.getItem("tempo-learning-authorities-v1")!); registry[client.studyWorkspace()].offlineDatabase = "another-database"; const bytes = JSON.stringify(registry); localStorage.setItem("tempo-learning-authorities-v1", bytes);
  await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential)).rejects.toMatchObject({ kind: "storage" }); expect(fetcher).toHaveBeenCalledTimes(1); expect(localStorage.getItem("tempo-learning-authorities-v1")).toBe(bytes);
});
it("pr291 R1 fresh unavailable connections and cross-origin requests cannot use another cache", async () => {
  const { client, fetcher } = await connected(); fetcher.mockRejectedValue(new TypeError("Failed to fetch"));
  await expect(client.connectStudy("http://other.test", "workspace-a", credential)).rejects.toMatchObject({ kind: "transport", cachedPracticeAvailable: false });
  expect(client.studyConnection()?.origin).toBe("http://learning.test:8001"); client.disconnectStudy();
  await expect(client.connectStudy("http://new.test", "workspace-a", credential)).rejects.toMatchObject({ kind: "transport", cachedPracticeAvailable: false }); expect(client.studyConnection()).toBeUndefined();
});
it.each(["success", "auth"])("pr291 superseded bootstrap %s cannot rebind or expire a newer connection", async stale => {
  const { client, fetcher } = await connected(); const reply = Promise.withResolvers<Response>(); fetcher.mockImplementationOnce(() => reply.promise);
  const first = client.connectStudy("http://first.test", "workspace-a", credential); const outcome = expect(first).rejects.toMatchObject({ kind: "superseded" }); await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  await client.connectStudy("http://latest.test", "workspace-a", credential); const expired = vi.fn(); window.addEventListener("tempo:study-auth-required", expired);
  try { reply.resolve(stale === "auth" ? new Response(null, { status: 401 }) : Response.json(bootstrap)); await outcome; expect(client.studyConnection()?.origin).toBe("http://latest.test"); expect(expired).not.toHaveBeenCalled(); }
  finally { window.removeEventListener("tempo:study-auth-required", expired); }
});
it("pr291 disconnect fences an unresolved bootstrap", async () => {
  const { client, fetcher } = await connected(); const reply = Promise.withResolvers<Response>(); fetcher.mockImplementationOnce(() => reply.promise);
  const pending = client.connectStudy("http://learning.test:8001", "workspace-a", credential); const outcome = expect(pending).rejects.toMatchObject({ kind: "superseded" }); await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2)); client.disconnectStudy(); reply.resolve(Response.json(bootstrap)); await outcome;
  expect(client.studyConnection()).toBeUndefined(); expect(sessionStorage.getItem("tempo-learning-connection-v1")).toBeNull();
});

it("pr291 R2 registered foreign offline work stays isolated during original resumption", async () => {
  const { client } = await connected(); await client.connectStudy("http://b.test", "workspace-a", credential);
  const { offlineTrainingDatabase } = await import("../../database/browser/offline-training-storage"); const database = await offlineTrainingDatabase();
  await new Promise<void>((resolve, reject) => { const transaction = database.transaction("training", "readwrite"); transaction.objectStore("training").put({ cards: [], attempts: [{ localEntryId: 1, completedAt: "original-time", outcome: "again" }] }, "prepared-daily-queue"); transaction.oncomplete = () => resolve(); transaction.onabort = () => reject(transaction.error); }); database.close();
  const before = await client.retainedStudyData();
  sessionStorage.setItem("tempo-learning-connection-v1", JSON.stringify({ origin: "http://learning.test:8001", workspace: "workspace-a", credential, bootstrap })); vi.resetModules();
  const restored = await import("../../app/lib/study-connection"); await restored.connectStudy("http://learning.test:8001", "workspace-a", credential); expect(await restored.retainedStudyData()).toEqual(before);
  await expect(restored.connectStudy("http://c.test", "workspace-a", credential)).rejects.toMatchObject({ kind: "authority" });
});
it("pr291 R1 an unsupported intent database version blocks cached fallback before fetching", async () => {
  const { client, fetcher } = await connected();
  await new Promise<void>((resolve, reject) => { const opening = indexedDB.open("tempo-study-intents", 2); opening.onupgradeneeded = () => opening.result.createObjectStore("retained-original"); opening.onsuccess = () => { opening.result.close(); resolve(); }; opening.onerror = () => reject(opening.error); });
  fetcher.mockRejectedValue(new TypeError("Failed to fetch")); await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential)).rejects.toMatchObject({ kind: "storage", cachedPracticeAvailable: false }); expect(fetcher).toHaveBeenCalledTimes(1);
});
it("pr291 R1 retained storage is revalidated after a transport outage", async () => {
  const { client, fetcher } = await connected(); const reply = Promise.withResolvers<Response>(); fetcher.mockImplementationOnce(() => reply.promise);
  const pending = client.connectStudy("http://learning.test:8001", "workspace-a", credential); const outcome = expect(pending).rejects.toMatchObject({ kind: "authority", cachedPracticeAvailable: false }); await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  localStorage.setItem("tempo-pending-tactic-capture-v1", "unscoped-original"); reply.reject(new TypeError("Failed to fetch")); await outcome; expect(localStorage.getItem("tempo-pending-tactic-capture-v1")).toBe("unscoped-original");
});

function pendingBootstrapBody() {
  const reading = Promise.withResolvers<void>();
  let bodyController: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      bodyController = controller;
      controller.enqueue(new TextEncoder().encode('{"schema_version":'));
    },
    pull() { reading.resolve(); },
  }, { highWaterMark: 0 });
  return { response: new Response(stream, { status: 200 }), reading: reading.promise,
    fail: (cause: unknown = new TypeError("Synthetic interrupted body")) => bodyController.error(cause) };
}

async function savedConnectionState(client: typeof import("../../app/lib/study-connection")) {
  return { connection: structuredClone(client.studyConnection()), origin: client.LEARNING_URL,
    session: sessionStorage.getItem("tempo-learning-connection-v1"),
    registry: localStorage.getItem("tempo-learning-authorities-v1"), retained: await client.retainedStudyData() };
}

it("P3-U1 post-header body transport failure retains original-authority cached practice and immutable commands", async () => {
  const { client, fetcher } = await connected();
  await retainAuthority(client.studyConnection()!.origin, "p3-original");
  const before = await savedConnectionState(client);
  const body = pendingBootstrapBody();
  expect(body.response.status).toBe(200); expect(body.response.bodyUsed).toBe(false);
  fetcher.mockResolvedValueOnce(body.response);
  const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  const outcome = expect(connecting).rejects.toMatchObject({ kind: "transport", cachedPracticeAvailable: true });
  await body.reading; expect(body.response.bodyUsed).toBe(true); body.fail(); await outcome;
  expect(await savedConnectionState(client)).toEqual(before);
});

it.each(["AbortError", "TimeoutError"])("P3-U2 %s during body consumption uses the original single five-second deadline", async name => {
  const { client, fetcher } = await connected();
  const before = await savedConnectionState(client);
  const timeout = new AbortController();
  const createTimeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(timeout.signal);
  const body = pendingBootstrapBody();
  fetcher.mockImplementationOnce((_input, options: RequestInit) => {
    expect(options.signal).toBe(timeout.signal);
    options.signal!.addEventListener("abort", () => body.fail(options.signal!.reason), { once: true });
    return Promise.resolve(body.response);
  });
  const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  const outcome = expect(connecting).rejects.toMatchObject({ kind: "transport", cachedPracticeAvailable: true });
  await body.reading; timeout.abort(new DOMException("Synthetic request deadline", name)); await outcome;
  expect(createTimeout).toHaveBeenCalledExactlyOnceWith(5_000);
  expect(await savedConnectionState(client)).toEqual(before);
});

it.each([
  ["P3-U3 malformed JSON", '{"schema_version":'],
  ["P3-U4 empty complete body", ""],
  ["P3-U5 unsupported schema", JSON.stringify({ ...bootstrap, schema_version: 2 })],
  ["P3-U5 missing setting", JSON.stringify({ ...bootstrap, settings: {} })],
  ["P3-U5 wrong setting type", JSON.stringify({ ...bootstrap, settings: { ...bootstrap.settings, study_only_mode: "false" } })],
  ["P3-U6 workspace mismatch", JSON.stringify({ ...bootstrap, workspace_id: "foreign" })],
  ["P3-U7 invalid timezone", JSON.stringify({ ...bootstrap, timezone: "Not/A_Timezone" })],
])("%s remains a fail-closed bootstrap error", async (_identity, text) => {
  const { client, fetcher } = await connected();
  const before = await savedConnectionState(client);
  fetcher.mockResolvedValueOnce(new Response(text, { status: 200 }));
  await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential))
    .rejects.toMatchObject({ kind: "bootstrap", cachedPracticeAvailable: false });
  expect(await savedConnectionState(client)).toEqual(before);
});

it.each([401, 403, 500])("P3-U8 HTTP %s never consumes an unreadable rejection body or permits cached practice", async status => {
  const { client, fetcher } = await connected();
  const before = await savedConnectionState(client);
  const body = pendingBootstrapBody();
  const response = new Response(body.response.body, { status });
  const readText = vi.spyOn(response, "text"); const readJson = vi.spyOn(response, "json");
  const authenticationRequired = vi.fn(); window.addEventListener("tempo:study-auth-required", authenticationRequired);
  try {
    fetcher.mockResolvedValueOnce(response);
    await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential))
      .rejects.toMatchObject({ kind: "http", status, cachedPracticeAvailable: false });
    expect(readText).not.toHaveBeenCalled(); expect(readJson).not.toHaveBeenCalled();
    expect(authenticationRequired).toHaveBeenCalledTimes(status === 500 ? 0 : 1);
    expect(await savedConnectionState(client)).toEqual(before);
  } finally { window.removeEventListener("tempo:study-auth-required", authenticationRequired); await response.body!.cancel(); }
});

it.each(["P3-U9 fresh authority", "P3-U10 different authority", "P3-U10 different authority with retained pending work", "registered recovery without a saved session"])(
  "%s cannot borrow cached authorization after a body interruption", async scenario => {
    const client = await import("../../app/lib/study-connection");
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(bootstrap))); vi.stubGlobal("fetch", fetcher);
    if (scenario !== "P3-U9 fresh authority") await client.connectStudy("http://learning.test:8001", "workspace-a", credential);
    if (scenario === "registered recovery without a saved session") client.disconnectStudy();
    const before = await savedConnectionState(client); const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
    const retainedPendingWork = scenario === "P3-U10 different authority with retained pending work";
    const connecting = client.connectStudy(scenario.startsWith("P3-U10") ? "http://other.test" : "http://learning.test:8001", "workspace-a", credential);
    const outcome = expect(connecting).rejects.toMatchObject({ kind: retainedPendingWork ? "authority" : "transport", cachedPracticeAvailable: false });
    await body.reading;
    expect(await savedConnectionState(client)).toEqual(before);
    if (retainedPendingWork) await retainAuthority("http://learning.test:8001", "original-authority-command");
    const beforeFailure = await savedConnectionState(client);
    body.fail(); await outcome; expect(await savedConnectionState(client)).toEqual(beforeFailure);
  });

it.each(["used", "locked"])("P3-U11 a previously %s body is a protocol failure without cached authorization", async state => {
  const { client, fetcher } = await connected(); const before = await savedConnectionState(client);
  const response = Response.json(bootstrap); const reader = state === "locked" ? response.body!.getReader() : undefined;
  if (state === "used") await response.text();
  fetcher.mockResolvedValueOnce(response);
  try {
    await expect(client.connectStudy("http://learning.test:8001", "workspace-a", credential))
      .rejects.toMatchObject({ kind: "bootstrap", cachedPracticeAvailable: false });
    expect(await savedConnectionState(client)).toEqual(before);
  } finally { reader?.releaseLock(); }
});

it.each(["replacement", "disconnect"])("P3-U12/U13 %s fences a delayed body failure without stale authentication or rebinding", async change => {
  const { client, fetcher } = await connected(); const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
  const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  const outcome = expect(connecting).rejects.toMatchObject({ kind: "superseded", cachedPracticeAvailable: false });
  await body.reading;
  if (change === "replacement") await client.connectStudy("http://latest.test", "workspace-a", "new-credential-" + "y".repeat(32));
  else client.disconnectStudy();
  await retainAuthority("http://learning.test:8001", "stale-original-command");
  const beforeFailure = await savedConnectionState(client); const authenticationRequired = vi.fn();
  window.addEventListener("tempo:study-auth-required", authenticationRequired);
  try { body.fail(); await outcome; expect(await savedConnectionState(client)).toEqual(beforeFailure); expect(authenticationRequired).not.toHaveBeenCalled(); }
  finally { window.removeEventListener("tempo:study-auth-required", authenticationRequired); }
  if (change === "disconnect") { expect(client.studyConnection()).toBeUndefined(); expect(sessionStorage.getItem("tempo-learning-connection-v1")).toBeNull(); }
});

it.each(["unregistered intent", "legacy command", "corrupt registry", "incompatible database"])(
  "P3-U14 body failure rechecks %s and preserves its rejection category and bytes", async changed => {
    const { client, fetcher } = await connected(); const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
    const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
    const expectedKind = ["corrupt registry", "incompatible database"].includes(changed) ? "storage" : "authority";
    const outcome = expect(connecting).rejects.toMatchObject({ kind: expectedKind, cachedPracticeAvailable: false });
    await body.reading;
    if (changed === "unregistered intent") await retainAuthority("http://unregistered.test", "foreign-original");
    if (changed === "legacy command") localStorage.setItem("tempo-pending-tactic-capture-v1", "original-bytes");
    if (changed === "corrupt registry") localStorage.setItem("tempo-learning-authorities-v1", "original-corruption");
    if (changed === "incompatible database") await new Promise<void>((resolve, reject) => {
      const opening = indexedDB.open("tempo-study-intents", 2);
      opening.onupgradeneeded = () => opening.result.createObjectStore("original-data");
      opening.onsuccess = () => { opening.result.close(); resolve(); }; opening.onerror = () => reject(opening.error);
    });
    const session = sessionStorage.getItem("tempo-learning-connection-v1"); const registry = localStorage.getItem("tempo-learning-authorities-v1");
    const retained = changed === "corrupt registry" ? undefined : await client.retainedStudyData();
    body.fail(); await outcome;
    expect(sessionStorage.getItem("tempo-learning-connection-v1")).toBe(session); expect(localStorage.getItem("tempo-learning-authorities-v1")).toBe(registry);
    if (retained) expect(await client.retainedStudyData()).toEqual(retained);
    if (changed === "legacy command") expect(localStorage.getItem("tempo-pending-tactic-capture-v1")).toBe("original-bytes");
  });

it("P3-U15 original resumption with registered foreign pending work survives a body interruption without claiming foreign commands", async () => {
  const { client, fetcher } = await connected(); await client.connectStudy("http://b.test", "workspace-a", credential);
  await client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  await retainAuthority("http://learning.test:8001", "a-original"); await retainAuthority("http://b.test", "b-original");
  const before = await savedConnectionState(client); const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
  const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  const outcome = expect(connecting).rejects.toMatchObject({ kind: "transport", cachedPracticeAvailable: true });
  await body.reading; body.fail(); await outcome; expect(await savedConnectionState(client)).toEqual(before);
});

it.each([new Error("Unknown body failure"), new DOMException("Body access denied", "SecurityError")])(
  "P3 unknown body exception %s remains actionable and cannot authorize cached practice", async cause => {
    const { client, fetcher } = await connected(); const before = await savedConnectionState(client);
    const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
    const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
    const outcome = expect(connecting).rejects.toMatchObject({ kind: "bootstrap", cachedPracticeAvailable: false, cause });
    await body.reading; body.fail(cause); await outcome; expect(await savedConnectionState(client)).toEqual(before);
  });

it.each(["workspace", "schema", "timezone"])("P3 invalid cached %s cannot authorize a post-header transport failure", async invalid => {
  const { client, fetcher } = await connected();
  Object.assign(client.studyConnection()!.bootstrap!, invalid === "workspace" ? { workspace_id: "wrong" } : invalid === "schema" ? { schema_version: 2 } : { timezone: "Not/AZone" });
  const before = await savedConnectionState(client); const body = pendingBootstrapBody(); fetcher.mockResolvedValueOnce(body.response);
  const connecting = client.connectStudy("http://learning.test:8001", "workspace-a", credential);
  const outcome = expect(connecting).rejects.toMatchObject({ kind: "bootstrap", cachedPracticeAvailable: false });
  await body.reading; body.fail(); await outcome; expect(await savedConnectionState(client)).toEqual(before);
});

it.each(['resolve','reject'])('study_priority_bootstrap_body_%s_retains_operation_hold_until_authority_settlement', async outcome => {
  const {client,fetcher}=await connected();
  const priority=await import('../../app/lib/study-priority');priority.setStudyPriorityScope(true);
  vi.useFakeTimers();
  const reading=Promise.withResolvers<void>();let controller!:ReadableStreamDefaultController<Uint8Array>;
  const body=new ReadableStream<Uint8Array>({start(value){controller=value;},pull(){reading.resolve();}},{highWaterMark:0});
  fetcher.mockResolvedValueOnce(new Response(body));
  const pending=client.connectStudy('http://learning.test:8001','workspace-a',credential).catch(error=>error);
  await reading.promise;
  await vi.advanceTimersByTimeAsync(3500);
  expect(priority.studyInteractionActive()).toBe(true);
  if(outcome==='resolve'){controller.enqueue(new TextEncoder().encode(JSON.stringify(bootstrap)));controller.close();}
  else controller.error(new TypeError('Interrupted bootstrap body'));
  const result=await pending;
  if(outcome==='resolve')expect(result.workspace_id).toBe('workspace-a');
  else expect(result).toMatchObject({kind:'transport',cachedPracticeAvailable:true});
  await vi.advanceTimersByTimeAsync(3000);expect(priority.studyInteractionActive()).toBe(false);
});
