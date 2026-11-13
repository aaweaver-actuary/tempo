import { expect, it, vi } from "vitest";
import type { IntentRecord, IntentStore } from "@tempo/browser-intents";
import type { StudyClient } from "../../app/lib/study-connection";
import { createStudyCommandDelivery } from "../../app/lib/study-command-client";

function fixture(started = false) {
  const record = { key: "scope:original", kind: "study-command", requestId: "original", attemptId: "attempt",
    endpoint: "/api/studies/s/exercises/e/attempts", revision: 1, data: { started }, payload: { scope: "scope", body: { attempt_id: "attempt", revision: 1, context: "practice" },
      headers: { "Idempotency-Key": "original", "Content-Type": "application/json" } } } as IntentRecord;
  const store = { update: vi.fn(async (_key, change) => change(record)), list: vi.fn(async () => []) } as unknown as IntentStore;
  return { record, store };
}
function client(transport: typeof fetch) {
  return { origin: "https://a.example", generation: 1, independent: true, learningFetch: transport } as unknown as StudyClient;
}

it("uses the supplied authority and store for POST and original receipt", async () => {
  const { record, store } = fixture();
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      expect(input).toBe(record.endpoint);
      expect(init.headers).toEqual({ "Idempotency-Key": "original", "Content-Type": "application/json" });
      expect(JSON.parse(String(init.body))).toEqual({ attempt_id: "attempt", revision: 1, context: "practice" });
      return Response.json({ operation_id: "original", state: "queued" }, { status: 202 });
    }
    expect(String(input)).toBe("https://a.example/api/operations/original");
    return Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt", assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true } });
  });
  const delivery = createStudyCommandDelivery(client(fetch), async () => store);
  expect((await delivery.deliver(record, new AbortController().signal)).state).toBe("committed");
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(store.update).toHaveBeenCalledOnce();
});

it("refuses receipt polling after the supplied authority changes during a 202 body read", async () => {
  const { record, store } = fixture();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let reading!: () => void;
  const entered = new Promise<void>(resolve => { reading = resolve; });
  const fetch = vi.fn(async () => {
    const response = new Response(null, { status: 202 });
    response.clone = () => ({ json: async () => { reading(); await gate; return { operation_id: "original" }; } }) as Response;
    return response;
  });
  const selected = client(fetch);
  const delivery = createStudyCommandDelivery(selected, async () => store);
  const pending = delivery.deliver(record, new AbortController().signal);
  await entered;
  Object.defineProperty(selected, "generation", { value: 2 });
  release();
  await expect(pending).rejects.toThrow("Study authority changed");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("checks each original receipt through its own supplied client", async () => {
  const { record, store } = fixture(true);
  const otherStore = fixture(true).store;
  const firstFetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt", assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true } }));
  const secondFetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => Response.json({ operation_id: "original", state: "complete", response: { attempt_id: "attempt", assessment: { outcome: "needs_self_assessment", feedback: "compare" }, pending_self_assessment: true } }));
  const first = client(firstFetch), second = client(secondFetch);
  Object.defineProperty(second, "origin", { value: "https://b.example" });
  const a = createStudyCommandDelivery(first, async () => store);
  const b = createStudyCommandDelivery(second, async () => otherStore);
  expect((await a.checkReceipt(record, new AbortController().signal))?.state).toBe("committed");
  expect((await b.checkReceipt(record, new AbortController().signal))?.state).toBe("committed");
  expect(String(firstFetch.mock.calls[0][0])).toBe("https://a.example/api/operations/original");
  expect(String(secondFetch.mock.calls[0][0])).toBe("https://b.example/api/operations/original");
});

it.each(["defense-command", "endgame-command"])("holds retired %s without posting and may inspect its original receipt", async kind => {
  const { record, store } = fixture();
  record.kind = kind;
  const fetch = vi.fn(async () => new Response(null, { status: 404 }));
  const delivery = createStudyCommandDelivery(client(fetch), async () => store);
  await expect(delivery.deliver(record, new AbortController().signal)).rejects.toThrow("retired exercise is held");
  expect(await delivery.checkReceipt(record, new AbortController().signal)).toBeUndefined();
  expect(fetch).toHaveBeenCalledOnce();
  expect(String(fetch.mock.calls[0][0])).toContain("/api/operations/original");
  expect(store.update).not.toHaveBeenCalled();
});


it.each(["missing", "foreign"])("%s Study acknowledgment cannot poll another operation or commit the command", async kind => {
  const { record, store } = fixture();
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(record.requestId);
    expect(init?.body).toBe(JSON.stringify({ attempt_id: "attempt", revision: 1, context: "practice" }));
    return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
  });
  const delivery = createStudyCommandDelivery(client(fetch), async () => store);
  await expect(delivery.deliver(record, new AbortController().signal)).rejects.toMatchObject({ name: "PendingOperationError", operationId: "original" });
  expect(fetch).toHaveBeenCalledOnce();
  expect(record.payload).toMatchObject({ body: { attempt_id: "attempt", revision: 1, context: "practice" } });
});

it('B31 retained pending practice presentation requires one original parent and its unreset boundary', async () => {
  const { studyIntentStore } = await import('../../database/browser/study-intent-storage');
  const { retainedPendingStudyPresentation } = await import('../../app/lib/study-command-client');
  const store = await studyIntentStore();
  const scope = 'study:b31-study:b31-exercise:practice:2', endpoint = '/api/studies/b31-study/exercises/b31-exercise/attempts';
  const make = async (id: string) => {
    const key = `${scope}:${id}`;
    await store.put({ key, kind: 'study-command', attemptId: id, requestId: id, endpoint, revision: 2, createdAt: 'original',
      payload: { scope, body: { attempt_id: id, revision: 2, context: 'practice' }, headers: { 'Idempotency-Key': id } } });
    await store.update(key, current => ({ ...current, state: 'committed', receipt: { attempt_id: id, pending_self_assessment: true } }));
  };
  await make('original-b31');
  expect(await retainedPendingStudyPresentation('b31-study', 'b31-exercise', endpoint)).toBe('original-b31');
  expect(await retainedPendingStudyPresentation('b31-study', 'b31-exercise', '/foreign')).toBeUndefined();
  await make('second-b31');
  await expect(retainedPendingStudyPresentation('b31-study', 'b31-exercise', endpoint)).rejects.toThrow('Multiple retained');
  await store.boundary(`practice-reset:${scope}`, record => (record.payload as { scope?: string })?.scope === scope, 0);
  expect(await retainedPendingStudyPresentation('b31-study', 'b31-exercise', endpoint)).toBeUndefined();
});

it.each(["initial", "receipt-first"])("C14 retained tactic %s rejects a foreign card and recovers the same frozen attempt", async lane => {
  const { webcrypto } = await import("node:crypto");
  vi.stubGlobal("crypto", webcrypto);
  try {
    const { record, store } = fixture(lane === "receipt-first");
    record.kind = "tactic-command";
    record.endpoint = "/api/tactics/attempt";
    record.payload = { scope: "tactic:deck:puzzle", headers: { "Idempotency-Key": "original" }, body: {
      attempt_id: "attempt", puzzle_id: "polish-1", deck_id: "deck", correct: true, clean: true,
      source_fen: "q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17",
      moves: ["e8d7", "a2e6", "d7d8", "f7f8"] } };
    const original = structuredClone(record.payload);
    let valid = false;
    const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual((original as { body: unknown }).body);
        return Response.json({ operation_id: "original", state: "queued" }, { status: 202 });
      }
      return Response.json({ operation_id: "original", state: "complete", response: {
        card_id: valid ? "7102eb92e00287f834ec6f5a2dd1afaaebfdafd3c649b975444a77ccff836d5a" : "foreign-card",
        mode: "normal", next_due: "2026-11-13" } });
    });
    const delivery = createStudyCommandDelivery(client(fetch), async () => store);
    const run = () => lane === "initial" ? delivery.deliver(record, new AbortController().signal)
      : delivery.checkReceipt(record, new AbortController().signal);
    await expect(run()).rejects.toThrow("original saved card");
    expect(record.payload).toEqual(original);
    valid = true;
    expect((await run())?.state).toBe("committed");
    expect(record.requestId).toBe("original");
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(lane === "initial" ? 2 : 0);
  } finally { vi.unstubAllGlobals(); }
});

it("C14 retained tactic refuses a request whose saved attempt identity differs", async () => {
  const { record, store } = fixture();
  record.kind = "tactic-command";
  record.payload = { scope: "tactic", headers: {}, body: { attempt_id: "foreign", source_fen: "invalid", moves: ["e2e4", "e7e5"] } };
  const fetch = vi.fn(async () => Response.json({ card_id: "card", mode: "normal", next_due: "2026-11-13" }));
  const delivery = createStudyCommandDelivery(client(fetch), async () => store);
  await expect(delivery.deliver(record, new AbortController().signal)).rejects.toThrow("cannot prove its original card");
  expect(record.requestId).toBe("original");
});

it.each(['missing', 'rejecting', 'authority-change'])('C14 retained tactic %s digest failure keeps the durable original and recovers once', async fault => {
  const { webcrypto } = await import('node:crypto');
  const { IDBFactory } = await import('fake-indexeddb');
  const { openIntentStore } = await import('@tempo/browser-intents');
  const { createStudySession } = await import('@tempo/study-session');
  const factory = new IDBFactory();
  const store = await openIntentStore({ workspace: 'original-workspace', databaseName: 'c14-tactic-digest', indexedDB: factory });
  const randomUUID = () => webcrypto.randomUUID();
  let enterDigest!: () => void, releaseDigest!: () => void;
  const entered = new Promise<void>(resolve => { enterDigest = resolve; });
  const gate = new Promise<void>(resolve => { releaseDigest = resolve; });
  const digest = fault === 'missing' ? undefined : fault === 'rejecting'
    ? vi.fn(async () => { throw new Error('digest unavailable'); })
    : vi.fn(async (algorithm: AlgorithmIdentifier, input: BufferSource) => {
        enterDigest(); await gate; return webcrypto.subtle.digest(algorithm, input as Parameters<typeof webcrypto.subtle.digest>[1]);
      });
  vi.stubGlobal('crypto', { randomUUID, ...(digest ? { subtle: { digest } } : {}) });
  const payload = { attempt_id: 'original-attempt', puzzle_id: 'polish-1', deck_id: 'deck', correct: true, clean: true,
    source_fen: 'q3k1nr/1pp1nQpp/3p4/1P2p3/4P3/B1PP1b2/B5PP/5K2 b k - 0 17',
    moves: ['e8d7', 'a2e6', 'd7d8', 'f7f8'] };
  const envelope = { key: 'tactic:original', kind: 'tactic-command', requestId: 'original-operation', attemptId: 'original-attempt',
    endpoint: 'https://a.example/api/tactics/attempt', createdAt: 'original-clock', revision: 1,
    payload: { scope: 'tactic:deck:polish-1', headers: { 'Idempotency-Key': 'original-operation' }, body: payload } };
  const original = structuredClone(envelope);
  const calls: { url: string; method: string; body?: unknown; key?: string | null }[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), method: init?.method ?? 'GET', ...(init?.body ? { body: JSON.parse(String(init.body)) } : {}),
      ...(init?.headers ? { key: new Headers(init.headers).get('Idempotency-Key') } : {}) });
    if (init?.method === 'POST') return Response.json({ operation_id: 'original-operation', state: 'queued' }, { status: 202 });
    return Response.json({ operation_id: 'original-operation', state: 'complete', response: {
      card_id: '7102eb92e00287f834ec6f5a2dd1afaaebfdafd3c649b975444a77ccff836d5a', mode: 'normal', next_due: '2026-11-13' } });
  });
  const selected = client(fetcher);
  const delivery = createStudyCommandDelivery(selected, async () => store);
  const session = createStudySession({ store, learning: delivery, owner: 'owned-test' });
  session.start(envelope.key);
  try {
    const pending = session.submit(envelope);
    if (fault === 'authority-change') {
      await entered;
      Object.defineProperty(selected, 'generation', { value: 2, configurable: true });
      releaseDigest();
    }
    await expect(pending).rejects.toThrow(fault === 'authority-change' ? 'Study authority changed' : fault === 'missing' ? 'verification is unavailable' : 'digest unavailable');
    const retained = await store.get(envelope.key);
    expect(retained).toMatchObject({ state: 'pending', requestId: original.requestId, attemptId: original.attemptId,
      endpoint: original.endpoint, createdAt: original.createdAt, payload: original.payload });
    expect(retained?.receipt).toBeUndefined();
    expect(calls.filter(call => call.method === 'POST')).toEqual([{ url: original.endpoint, method: 'POST', body: payload, key: original.requestId }]);
    vi.stubGlobal('crypto', webcrypto);
    expect((await session.retrySave(envelope.key)).state).toBe('committed');
    expect(await store.get(envelope.key)).toMatchObject({ state: 'committed', requestId: original.requestId,
      attemptId: original.attemptId, payload: original.payload, receipt: { card_id: '7102eb92e00287f834ec6f5a2dd1afaaebfdafd3c649b975444a77ccff836d5a' } });
    expect(calls.filter(call => call.method === 'POST')).toHaveLength(1);
    expect(calls.filter(call => call.method === 'GET').every(call => call.url === 'https://a.example/api/operations/original-operation')).toBe(true);
  } finally {
    releaseDigest(); session.dispose(); store.close(); vi.unstubAllGlobals();
  }
});

it.each(["initial", "receipt-first"])("C21 %s holds a matching attempt with a foreign review context", async lane => {
  const { record, store } = fixture(lane === "receipt-first");
  const receipt = { attempt_id: "attempt", assessment: { outcome: "correct", feedback: "ok" },
    rating: "correct", requested_rating: "correct", persisted: true,
    review: { review_id: 7, requeue_entry_id: null } };
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => init?.method === "POST"
    ? Response.json(receipt)
    : Response.json({ operation_id: "original", state: "complete", response: receipt }));
  const delivery = createStudyCommandDelivery(client(fetcher), async () => store);
  await expect(lane === "initial" ? delivery.deliver(record, new AbortController().signal)
    : delivery.checkReceipt(record, new AbortController().signal)).rejects.toThrow("context");
  expect(record.requestId).toBe("original");
  expect((record.payload as { body: { context: string } }).body.context).toBe("practice");
});

it("C21 holds an unknown retained endpoint without posting its original body", async () => {
  const { record, store } = fixture(true);
  record.endpoint = "/api/studies/s/exercises/e/author";
  const original = structuredClone(record.payload);
  const fetcher = vi.fn(async () => new Response(null, { status: 404 }));
  const delivery = createStudyCommandDelivery(client(fetcher), async () => store);
  await expect(delivery.deliver(record, new AbortController().signal)).rejects.toThrow("held");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(String(fetcher.mock.calls[0][0])).toContain("/api/operations/original");
  expect(record.payload).toEqual(original);
});
