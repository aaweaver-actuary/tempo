import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import manifestFixture from "../fixtures/opening-evidence-manifest.json";
import { openingDecisionManifestSchema, openingEvidenceCheckpointSchema } from "../../app/domain/opening-evidence";
import type { OpeningEvidenceCheckpoint } from "../../app/domain/opening-evidence";

const manifest = openingDecisionManifestSchema.parse(manifestFixture);
const checkpoint: OpeningEvidenceCheckpoint = {
  attempt_id: "background-attempt", manifest, origin_queue_entry_id: 101, queue_entry_id: 101,
  parent_attempt_id: null, source: "live", study_timezone: "UTC", started_at: "2026-09-30T12:00:00Z",
  terminal: { state: "partial", final_sequence: 1, ended_at: "2026-09-30T12:01:00Z" },
  events: [{ sequence: 1, decision_index: 0, decision_id: manifest.decisions[0].decision_id,
    expected_uci: "e2e4", response_uci: "e2e4", kind: "first_response", disposition: "expected",
    observed_at: "2026-09-30T12:00:30Z", assistance: null }],
};
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

// Async storage seam; real IndexedDB transaction/reload behavior remains browser-covered.
async function savedJournal(complete = false) {
  vi.resetModules();
  vi.stubGlobal("indexedDB", {});
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal("navigator", { onLine: true, locks: {
    request: vi.fn(async (_name, _options, callback) => callback({})),
  } });
  vi.stubGlobal("localStorage", { getItem: () => null });
  const { events, ...header } = structuredClone(checkpoint);
  const digest = await webcrypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(openingEvidenceCheckpointSchema.parse(checkpoint))));
  const operationKey = `opening-checkpoint:${Buffer.from(digest).toString("hex")}`;
  const originalDelivery = { checkpoint: openingEvidenceCheckpointSchema.parse(checkpoint), operationKey };
  const attempts = new Map([[header.attempt_id, { ...header, owner_session_id: "previous-browser",
    final_sequence: 1, delivery_state: complete ? "idle" : "pending",
    terminal: { ...header.terminal!, state: complete ? "complete" : "partial" },
    delivery: undefined as typeof originalDelivery | undefined }]]);
  const savedEvents = new Map(events.map(event => [JSON.stringify([header.attempt_id, event.sequence]),
    { ...event, attempt_id: header.attempt_id }]));
  const stores: Record<string, Map<unknown, unknown>> = { opening_attempts: attempts, opening_events: savedEvents, training: new Map() };
  const database = {
    transaction: () => {
      let pendingRequests = 0;
      const transaction = { oncomplete: null as (() => void) | null, objectStore: (name: string) => {
        const rows = stores[name];
        const request = (action: () => unknown) => {
          pendingRequests += 1;
          const result = { result: undefined as unknown, onsuccess: null as (() => void) | null };
          queueMicrotask(() => {
            result.result = structuredClone(action()); result.onsuccess?.();
            if (--pendingRequests === 0) queueMicrotask(() => { if (!pendingRequests) transaction.oncomplete?.(); });
          });
          return result;
        };
        return {
          openCursor: () => {
            const values = [...rows.values()]; let position = 0;
            const cursor = { result: null as unknown, onsuccess: null as (() => void) | null };
            const advance = () => queueMicrotask(() => {
              cursor.result = position < values.length ? { value: structuredClone(values[position++]), continue: advance } : null;
              cursor.onsuccess?.();
            });
            advance(); return cursor;
          },
          get: (key: unknown) => request(() => rows.get(key)),
          getAll: () => request(() => [...rows.values()]),
          put: (value: { attempt_id: string }) => request(() => rows.set(value.attempt_id, structuredClone(value))),
          delete: (key: unknown) => request(() => rows.delete(Array.isArray(key) ? JSON.stringify(key) : key)),
          index: (field: string) => {
            const matching = (key: unknown) => [...rows.values()].filter(row =>
              (row as Record<string, unknown>)[field] === key);
            return { get: (key: unknown) => request(() => matching(key)[0]),
              getAll: (key: unknown) => request(() => matching(key)), count: (key: unknown) => request(() => matching(key).length) };
          },
        };
      } };
      return transaction;
    },
  } as unknown as IDBDatabase;
  const storage = await import("../../database/browser/offline-training-storage");
  vi.spyOn(storage, "offlineTrainingDatabase").mockResolvedValue(database);
  return { journal: await import("../../app/lib/opening-evidence-journal"), attempts, savedEvents, originalDelivery };
}

it("AS-15 orphan evidence verification uses background HTTP admission", async () => {
  const { journal } = await savedJournal(true);
  const fetching = vi.fn(async (url: string) => url.includes("/attempts/")
    ? Response.json({ detail: "Not persisted" }, { status: 404 })
    : Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 }));
  vi.stubGlobal("fetch", fetching);
  await journal.recoverOpeningEvidence();
  const verification = fetching.mock.calls.find(([url]) => url.endsWith(`/attempts/${checkpoint.attempt_id}`));
  expect(verification).toBeDefined();
  expect(new Headers((verification as unknown as [string, RequestInit])[1]?.headers).get("X-Tempo-Work-Class")).toBe("background");
  expect(fetching.mock.calls.some(([url]) => url.endsWith("/review"))).toBe(false);
});

it.each([false, true])("AS-15 background checkpoint receipt polling remains background after HTTP 202 (pending retry=%s)", async pendingFirst => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const requests: { url: string; init?: RequestInit }[] = [];
  let receiptReads = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (init?.method === "POST") return Response.json({ operation_id: originalDelivery.operationKey, state: "queued" }, { status: 202 });
    receiptReads += 1;
    return Response.json(pendingFirst && receiptReads === 1 ? { state: "pending" } : { state: "complete", response: {
      persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1,
    } });
  }));
  if (pendingFirst) {
    await expect(journal.flushOpeningEvidence()).rejects.toMatchObject({ operationId: originalDelivery.operationKey });
    expect(attempts.get(checkpoint.attempt_id)?.delivery).toEqual(originalDelivery);
  }
  await journal.flushOpeningEvidence();
  expect(requests).toHaveLength(pendingFirst ? 3 : 2);
  for (const [index, request] of requests.entries()) {
    expect(new Headers(request.init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (index === 0) {
      expect(request.url).toMatch(/\/opening-evidence\/checkpoints$/);
      expect(new Headers(request.init?.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
      expect(JSON.parse(request.init!.body as string)).toEqual(originalDelivery.checkpoint);
    } else expect(request.url).toMatch(new RegExp(`/operations/${encodeURIComponent(originalDelivery.operationKey)}$`));
  }
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});

it("foreground review completion and operation receipt reads retain foreground admission by default", async () => {
  const requests: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    return init?.method === "POST" ? Response.json({ operation_id: "review-key", state: "queued" }, { status: 202 })
      : Response.json({ state: "complete", response: { persisted: true } });
  }));
  const { saveEvidenceAwareReview } = await import("../../app/lib/opening-evidence-review");
  const response = await saveEvidenceAwareReview({ endpoint: "/api/cards/card/review", operationKey: "review-key",
    body: { attempt_id: "review-attempt", outcome: "correct", queue_entry_id: 101 }, onEvidenceRejected: vi.fn() });
  expect(await response.json()).toEqual({ persisted: true });
  expect(requests.map(request => request.url)).toEqual(["/api/cards/card/review", expect.stringMatching(/\/operations\/review-key$/)]);
  expect(requests.every(request => !new Headers(request.init?.headers).has("X-Tempo-Work-Class"))).toBe(true);
  expect(requests.every(request => request.init?.signal === undefined)).toBe(true);
});


it.each([202, 500])("AS-15 checkpoint receipt polling is bounded by the delivery timeout (POST %s)", async postStatus => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const requests: { url: string; init?: RequestInit }[] = [];
  let receiptStarted!: () => void;
  const enteredReceipt = new Promise<void>(resolve => { receiptStarted = resolve; });
  let releaseReceipt!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (init?.method === "POST") return Promise.resolve(Response.json({ operation_id: originalDelivery.operationKey, state: "queued" }, { status: postStatus }));
    return new Promise<Response>((resolve, reject) => {
      releaseReceipt = resolve;
      init?.signal?.addEventListener("abort", () => reject(new DOMException("Delivery aborted", "AbortError")), { once: true });
      receiptStarted();
    });
  }));
  let failure: unknown;
  const flushing = journal.flushOpeningEvidence().catch(error => { failure = error; });
  await Promise.race([enteredReceipt, flushing]);
  try {
    expect(requests[1]).toBeDefined();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(failure).toMatchObject({ name: "AbortError" });
    expect(vi.getTimerCount()).toBe(0);
    expect(requests[1].init?.signal).toBe(requests[0].init?.signal);
    expect(requests[1].init?.signal?.aborted).toBe(true);
    expect(attempts.get(checkpoint.attempt_id)?.delivery).toEqual(originalDelivery);
    expect(savedEvents.size).toBe(1);
  } finally {
    // Release the baseline's unbounded fetch too, so a failing proof leaks no flush.
    releaseReceipt?.(Response.json({ state: "pending" }));
    await flushing;
  }
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    return Response.json(init?.method === "POST"
      ? { persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 }
      : { state: "complete", response: { persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 } });
  }));
  await journal.flushOpeningEvidence();
  const posts = requests.filter(request => request.init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(new Headers(posts[0].init?.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
  expect(posts[0].init?.body).toBe(JSON.stringify(originalDelivery.checkpoint));
  expect(requests.at(-1)?.url).toContain(encodeURIComponent(originalDelivery.operationKey));
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});

it.each(["immediate", "deferred"])("AS-15 immediate and deferred durable checkpoint failure both quarantine and advance the evidence queue (%s)", async timing => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const nextCheckpoint = { ...structuredClone(checkpoint), attempt_id: "next-valid-attempt" };
  attempts.set(nextCheckpoint.attempt_id, { ...attempts.get(checkpoint.attempt_id)!, attempt_id: nextCheckpoint.attempt_id,
    delivery: undefined });
  savedEvents.set(JSON.stringify([nextCheckpoint.attempt_id, 1]), { ...nextCheckpoint.events[0], attempt_id: nextCheckpoint.attempt_id });
  const requests: { url: string; init?: RequestInit }[] = [];
  const error = { status_code: 409, message: "Durable handler failed", detail: {
    code: "opening_evidence_conflict", message: "Immutable manifest differs", aggregate_review_allowed: true,
  } };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (init?.method !== "POST") return Response.json({ state: "failed", error });
    const body = JSON.parse(init.body as string) as OpeningEvidenceCheckpoint;
    if (body.attempt_id === checkpoint.attempt_id) return timing === "immediate"
      ? Response.json({ detail: error.detail }, { status: 409 })
      : Response.json({ operation_id: originalDelivery.operationKey, state: "queued" }, { status: 202 });
    return Response.json({ persisted: true, attempt_id: body.attempt_id, received_sequences: [1], contiguous_sequence: 1 });
  }));
  await journal.flushOpeningEvidence();
  expect(requests).toHaveLength(timing === "immediate" ? 2 : 3);
  if (timing === "deferred") expect(requests[1].url).toMatch(new RegExp(`/operations/${encodeURIComponent(originalDelivery.operationKey)}$`));
  else expect(requests.every(request => request.init?.method === "POST")).toBe(true);
  expect(requests.every(request => new Headers(request.init?.headers).get("X-Tempo-Work-Class") === "background")).toBe(true);
  expect(new Headers(requests[0].init?.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
  expect(JSON.parse(requests[0].init!.body as string)).toEqual(originalDelivery.checkpoint);
  expect(JSON.parse(requests.at(-1)!.init!.body as string).attempt_id).toBe(nextCheckpoint.attempt_id);
  expect(attempts.get(checkpoint.attempt_id)).toMatchObject({ delivery_state: "rejected",
    rejection: timing === "immediate" ? JSON.stringify({ detail: error.detail }) : error.message, delivery: originalDelivery });
  expect(attempts.has(nextCheckpoint.attempt_id)).toBe(false);
  expect([...savedEvents.values()]).toEqual([{ ...checkpoint.events[0], attempt_id: checkpoint.attempt_id }]);
});

it.each(["unknown", "pending", "retrying", "blocked", "missing", "network"])("AS-15 ambiguous checkpoint HTTP failure retains frozen delivery for unchanged retry (%s)", async state => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const requests: { url: string; init?: RequestInit }[] = [];
  let retry = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (retry) return Response.json(init?.method === "POST"
      ? { persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 }
      : { state: "complete", response: { persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 } });
    if (init?.method === "POST") return Response.json({ detail: "Proxy failure" }, { status: 500 });
    if (state === "network") throw new TypeError("Receipt network unavailable");
    return Response.json({ state, last_error: { message: "Service must be repaired" } }, { status: state === "missing" ? 404 : 200 });
  }));
  const error = await journal.flushOpeningEvidence().then(() => null, failure => failure);
  expect(requests).toHaveLength(2);
  expect(error).toMatchObject(state === "network" ? { name: "TypeError" } : { name: "PendingOperationError", operationId: originalDelivery.operationKey, blocked: state === "blocked" });
  if (state === "blocked") expect(error.message).toContain("Service must be repaired");
  expect(attempts.get(checkpoint.attempt_id)).toMatchObject({ delivery_state: "pending", delivery: originalDelivery });
  expect(savedEvents.size).toBe(1);
  retry = true;
  await journal.flushOpeningEvidence();
  expect(requests[2].url).toContain(encodeURIComponent(originalDelivery.operationKey));
  expect(requests.filter(request => request.init?.method === "POST")).toHaveLength(1);
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});

it.each(["unknown", "missing"])("AS-15 frozen checkpoint replays a missing admission with unchanged body and identity (%s)", async state => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const posts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method !== "POST") return state === "unknown"
      ? Response.json({ state: "unknown" }) : new Response(null, { status: 404 });
    posts.push(init);
    return posts.length === 1 ? new Response(null, { status: 500 }) : Response.json({
      persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1,
    });
  }));
  await expect(journal.flushOpeningEvidence()).rejects.toMatchObject({ name: "PendingOperationError" });
  expect(attempts.get(checkpoint.attempt_id)).toMatchObject({ delivery: originalDelivery });
  await journal.flushOpeningEvidence();
  expect(posts).toHaveLength(2);
  expect(posts[1].body).toBe(posts[0].body);
  expect(new Headers(posts[1].headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});

it("AS-15 durable complete receipt resolves an ambiguous checkpoint HTTP failure", async () => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const requests: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    return init?.method === "POST" ? Response.json({ detail: "Proxy failure" }, { status: 500 }) : Response.json({ state: "complete", response: {
      persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1,
    } });
  }));
  await journal.flushOpeningEvidence();
  expect(requests).toHaveLength(2);
  expect(requests[1].url).toMatch(new RegExp(`/operations/${encodeURIComponent(originalDelivery.operationKey)}$`));
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});


it("foreground receipt polling forwards an optional caller signal without changing admission", async () => {
  const controller = new AbortController();
  const fetching = vi.fn<typeof fetch>(async () =>
    Response.json({ operation_id: "foreground-key", state: "complete", response: { persisted: true } }));
  const { confirmOriginalOperationResponse } = await import("../../app/lib/operation-status");
  await confirmOriginalOperationResponse(Response.json({ operation_id: "foreground-key", state: "pending" }, { status: 202 }),
    "foreground-key", { signal: controller.signal, fetch: fetching });
  expect(fetching).toHaveBeenCalledOnce();
  const [url, init] = fetching.mock.calls[0];
  expect(String(url)).toMatch(/\/api\/operations\/foreground-key$/);
  expect(init?.signal).toBe(controller.signal);
  expect(new Headers(init?.headers).has("X-Tempo-Work-Class")).toBe(false);
});

it.each(["missing", "foreign"])("%s checkpoint acknowledgment retains frozen key and bytes across later journal events", async kind => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const calls: { url: string; init?: RequestInit }[] = [];
  let retry = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (init?.method !== "POST") return Response.json({ state: retry && calls.filter(call => call.init?.method === "POST").length > 1 ? "pending" : "unknown" });
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
    expect(new Headers(init.headers).get("X-Tempo-Work-Class")).toBe("background");
    expect(init.body).toBe(JSON.stringify(originalDelivery.checkpoint));
    if (!retry) return Response.json(kind === "foreign" ? { operation_id: "foreign", state: "queued" } : { state: "queued" }, { status: 202 });
    return Response.json({ operation_id: originalDelivery.operationKey, state: "queued" }, { status: 202 });
  }));
  await expect(journal.flushOpeningEvidence()).rejects.toMatchObject({ name: "PendingOperationError", operationId: originalDelivery.operationKey });
  expect(attempts.get(checkpoint.attempt_id)?.delivery).toEqual(originalDelivery);
  expect(calls).toHaveLength(1);
  savedEvents.set(JSON.stringify([checkpoint.attempt_id, 2]), { ...checkpoint.events[0], attempt_id: checkpoint.attempt_id, sequence: 2 });
  retry = true;
  await expect(journal.flushOpeningEvidence()).rejects.toMatchObject({ name: "PendingOperationError", operationId: originalDelivery.operationKey });
  expect(attempts.get(checkpoint.attempt_id)?.delivery).toEqual(originalDelivery);
  const posts = calls.filter(call => call.init?.method === "POST");
  expect(posts).toHaveLength(2);
  expect(posts[1].init?.body).toBe(posts[0].init?.body);
  expect(calls.some(call => call.url.includes("foreign"))).toBe(false);
});

it.each(['different locator', 'same locator generation'])('FE25 delayed evidence response preserves original journal after %s switch', async mode => {
  const { journal, attempts, savedEvents } = await savedJournal();
  let generation = 1;
  const binding = { locator: mode === 'different locator' ? 'authority-A' : 'same-locator',
    requireCurrent: () => { if (generation !== 1) throw new Error('authority changed'); } };
  const owner = await import('../../database/browser/offline-training-storage');
  vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockReturnValue(binding);
  let resolveResponse!: (response: Response) => void;
  let notifyStarted!: () => void;
  const started = new Promise<void>(resolve => { notifyStarted = resolve; });
  const fetcher = vi.fn(() => { notifyStarted(); return new Promise<Response>(resolve => { resolveResponse = resolve; }); });
  vi.stubGlobal('fetch', fetcher);
  const pending = journal.flushOpeningEvidence();
  await started;
  const original = structuredClone(attempts.get(checkpoint.attempt_id));
  const events = structuredClone([...savedEvents]);
  generation = 2;
  resolveResponse(Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 }));
  await expect(pending).rejects.toThrow('authority changed');
  expect(attempts.get(checkpoint.attempt_id)).toEqual(original);
  expect([...savedEvents]).toEqual(events);
  expect(fetcher).toHaveBeenCalledOnce();
});

it('FE25 delayed journal append keeps its captured authority and original events', async () => {
  const { journal } = await savedJournal();
  const owner = await import('../../database/browser/offline-training-storage');
  const storage = await import('../../database/browser/opening-evidence-journal-storage');
  let generation = 1;
  const binding = { locator: 'authority-A', requireCurrent: () => { if (generation !== 1) throw new Error('authority changed'); } };
  vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockReturnValue(binding);
  let release!: () => void, notifyStarted!: () => void;
  const started = new Promise<void>(resolve => { notifyStarted = resolve; });
  vi.spyOn(storage, 'retainOpeningAttempt').mockImplementation(() => { notifyStarted(); return new Promise<void>(resolve => { release = resolve; }); });
  const append = vi.spyOn(storage, 'appendOpeningAttempt');
  const prior = journal.retainOpeningEvidenceForStorageFallback(checkpoint);
  await started;
  const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
  const card = mapQueueCardToPracticeCard({ id: 'held-card', queue_entry_id: 101, start_fen: manifest.decisions[0].fen,
    moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening', repertoire_name: 'Held',
    repertoire_source: 'PGN', opening_decision_manifest: manifest });
  const capture = journal.beginOpeningAttempt(card, 'held-original')!;
  capture.response(0, 'e2e4', 'expected');
  generation = 2; release();
  await expect(prior).rejects.toThrow('authority changed');
  await vi.waitFor(() => expect(capture.storageError).toContain('authority changed'));
  expect(append).not.toHaveBeenCalled();
  expect(capture.uncommittedEvents.get(1)).toMatchObject({ response_uci: 'e2e4' });
});

it('FE25 position reservation holds its original authority while waiting for its lock', async () => {
  const { journal } = await savedJournal();
  const owner = await import('../../database/browser/offline-training-storage');
  let generation = 1;
  vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockReturnValue({ locator: 'authority-A',
    requireCurrent: () => { if (generation !== 1) throw new Error('authority changed'); } });
  const open = vi.mocked(owner.offlineTrainingDatabase); open.mockClear();
  let resume!: () => void;
  vi.stubGlobal('navigator', { onLine: true, locks: { request: vi.fn((_name, _options, callback) =>
    new Promise<void>(resolve => { resume = () => { void callback({}).then(resolve); }; })) } });
  const pending = journal.preparePositionOpeningAttempt({ positionStudyMode: 'position', positionPresentation: { attempt_id: 'position-original' } } as never);
  generation = 2; resume();
  expect(await pending).toContain('could not restore');
  expect(open).not.toHaveBeenCalled();
});

it.each(['original', 'foreign', 'conflicting'])('FE25 reconnect %s authority preserves or recovers exact unsaved opening events', async target => {
  const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const state = await openingEvidenceStorage();
  const owner = await import('../../database/browser/offline-training-storage');
  let generation = 1, authority = 'A';
  vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockImplementation(() => {
    const originalGeneration = generation, originalAuthority = authority;
    return { locator: `locator-${originalAuthority}`, sameAuthority: () => authority === originalAuthority,
      requireCurrent: () => { if (generation !== originalGeneration || authority !== originalAuthority) throw new Error('authority changed'); } };
  });
  const journal = await import('../../app/lib/opening-evidence-journal');
  const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
  const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
    start_fen: manifest.decisions[0].fen, moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening',
    repertoire_name: 'Held', repertoire_source: 'PGN', opening_decision_manifest: manifest });
  const capture = journal.beginOpeningAttempt(card, 'reconnect-original')!;
  capture.response(0, manifest.decisions[0].expected_uci, 'expected');
  await vi.waitFor(() => expect(state.stores.opening_events.size).toBe(1));
  state.controls.beforePut = store => store === 'opening_events' ? new DOMException('quota', 'QuotaExceededError') : undefined;
  capture.response(2, manifest.decisions[1].expected_uci, 'expected');
  await vi.waitFor(() => expect(capture.storageError).toContain('quota'));
  const original = structuredClone(capture.snapshot());
  const saved = structuredClone([...state.stores.opening_events]);
  state.controls.beforePut = undefined; generation = 2; authority = target === 'foreign' ? 'B' : 'A';
  if (target === 'conflicting') {
    const record = state.stores.opening_attempts.get('reconnect-original') as Record<string, unknown>;
    state.stores.opening_attempts.set('reconnect-original', { ...record, owner_session_id: 'foreign-owner' });
  }
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
  await journal.recoverOpeningEvidence().catch(() => undefined);
  expect(capture.snapshot()).toEqual(original);
  if (target === 'original') {
    expect(capture.storageError).toBeUndefined();
    expect(capture.uncommittedEvents.size).toBe(0);
    const events = [...state.stores.opening_events.values()].map(row => { const { attempt_id: _id, ...event } = row as typeof original.events[number] & { attempt_id: string }; return event; });
    expect(events).toEqual(original.events);
    expect((state.stores.opening_attempts.get('reconnect-original') as { attempt_id: string }).attempt_id).toBe('reconnect-original');
  } else {
    expect([...state.stores.opening_events]).toEqual(saved);
    expect(capture.uncommittedEvents.get(2)).toEqual(original.events[1]);
    if (target === 'foreign') expect(capture.storageError).toContain('quota');
    else { expect(capture.rebindHeld).toBe(true); expect(capture.storageError).toContain('remains held'); }
  }
});

it.each(['partial', 'complete'])('FE25 %s capture survives a failed terminal append until its exact snapshot commits', async terminal => {
  const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const state = await openingEvidenceStorage();
  vi.stubGlobal('navigator', { onLine: false });
  const journal = await import('../../app/lib/opening-evidence-journal');
  const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
  const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
    start_fen: manifest.decisions[0].fen, moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening',
    repertoire_name: 'Held', repertoire_source: 'PGN', opening_decision_manifest: manifest });
  const capture = journal.beginOpeningAttempt(card, `terminal-${terminal}`)!;
  state.controls.beforePut = () => new DOMException('quota', 'QuotaExceededError');
  capture.response(0, manifest.decisions[0].expected_uci, 'expected');
  if (terminal === 'complete') journal.completeOpeningAttempt(`terminal-${terminal}`);
  journal.partialOpeningAttempt(`terminal-${terminal}`);
  await vi.waitFor(() => expect(capture.storageError).toContain('quota'));
  const original = structuredClone(capture.snapshot());
  expect(journal.beginOpeningAttempt(card, `terminal-${terminal}`)).toBe(capture);
  expect(capture.uncommittedEvents.get(1)).toEqual(original.events[0]);
  state.controls.beforePut = undefined;
  if (terminal === 'complete') journal.partialOpeningAttempt(`terminal-${terminal}`);
  else { vi.stubGlobal('navigator', { onLine: true }); await journal.recoverOpeningEvidence().catch(() => undefined); }
  await vi.waitFor(() => expect((state.stores.opening_attempts.get(`terminal-${terminal}`) as { terminal?: unknown })?.terminal).toEqual(original.terminal));
  expect([...state.stores.opening_events.values()]).toContainEqual({ ...original.events[0], attempt_id: `terminal-${terminal}` });
});

it.each(['reconnect', 'exposed', 'foreign', 'no-lock', 'ambiguous', 'exposure-during-write', 'phone-existing', 'phone-uncertain', 'phone-unknown-entry', 'legacy', 'lock-error', 'lock-denied'] as const)(
  'FE25 first aborted append %s only recovers with fresh exclusive unexposed ownership', async target => {
    const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
    const state = await openingEvidenceStorage();
    const owner = await import('../../database/browser/offline-training-storage');
    let generation = 1, authority = 'A';
    vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockImplementation(() => {
      const originalGeneration = generation, originalAuthority = authority;
      return { locator: `locator-${originalAuthority}`, sameAuthority: () => authority === originalAuthority,
        requireCurrent: () => { if (generation !== originalGeneration || authority !== originalAuthority) throw new Error('authority changed'); } };
    });
    if (target === 'no-lock') vi.stubGlobal('navigator', { onLine: true });
    if (target === 'lock-error') vi.stubGlobal('navigator', { onLine: true, locks: { request: () => { throw new Error('lock unavailable'); } } });
    if (target === 'lock-denied') vi.stubGlobal('navigator', { onLine: true, locks: { request: async (_name: string, _options: unknown, callback: (lock: null) => void) => callback(null) } });
    const storage = await import('../../database/browser/opening-evidence-journal-storage');
    const journal = await import('../../app/lib/opening-evidence-journal');
    const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
    const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
      start_fen: manifest.decisions[0].fen, moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening',
      repertoire_name: 'Held', repertoire_source: 'PGN', opening_decision_manifest: manifest });
    state.controls.beforePut = () => new DOMException('first abort', 'QuotaExceededError');
    if (target === 'ambiguous') vi.spyOn(storage, 'appendOpeningAttempt').mockRejectedValueOnce(new Error('unknown outcome'));
    const capture = journal.beginOpeningAttempt(card, `fresh-${target}`, { freshAttempt: target !== 'legacy' })!;
    capture.response(0, manifest.decisions[0].expected_uci, 'expected');
    await vi.waitFor(() => expect(capture.storageError).toBeDefined());
    const original = structuredClone(capture.snapshot());
    expect(state.stores.opening_attempts.size).toBe(0);
    if (target === 'exposed') journal.markOpeningEvidenceExposed(original.attempt_id);
    if (target === 'phone-existing') state.stores.training.set('prepared-daily-queue', { attempts: [{ attemptId: original.attempt_id }] });
    if (target === 'phone-uncertain') state.stores.training.set('prepared-daily-queue', { attempts: 'malformed' });
    if (target === 'phone-unknown-entry') state.stores.training.set('prepared-daily-queue', { attempts: [{}] });
    state.controls.beforePut = undefined;
    generation = 2;
    if (target === 'foreign') authority = 'B';
    if (target === 'exposure-during-write') state.controls.beforePut = () => { journal.markOpeningEvidenceExposed(original.attempt_id); return undefined; };
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await journal.recoverOpeningEvidence().catch(() => undefined);
    expect(capture.snapshot()).toEqual(original);
    if (target === 'reconnect') {
      expect(capture.uncommittedEvents.size).toBe(0);
      expect((state.stores.opening_attempts.get(original.attempt_id) as { attempt_id: string }).attempt_id).toBe(original.attempt_id);
      const events = [...state.stores.opening_events.values()].map(row => { const { attempt_id: _id, ...event } = row as typeof original.events[number] & { attempt_id: string }; return event; });
      expect(events).toEqual(original.events);
    } else {
      expect(capture.uncommittedEvents.get(1)).toEqual(original.events[0]);
      expect(state.stores.opening_attempts.size).toBe(0);
      expect(state.stores.opening_events.size).toBe(0);
    }
  },
);

it('FE25 an ambiguously acknowledged retry cannot reuse an older abort as absence proof', async () => {
  const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const state = await openingEvidenceStorage();
  const owner = await import('../../database/browser/offline-training-storage');
  let generation = 1;
  vi.spyOn(owner, 'captureOfflineDatabaseBinding').mockImplementation(() => {
    const captured = generation;
    return { locator: 'same-authority', sameAuthority: () => true,
      requireCurrent: () => { if (generation !== captured) throw new Error('authority changed'); } };
  });
  const journal = await import('../../app/lib/opening-evidence-journal');
  const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
  const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
    start_fen: manifest.decisions[0].fen, moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening',
    repertoire_name: 'Held', repertoire_source: 'PGN', opening_decision_manifest: manifest });
  state.controls.beforePut = () => new DOMException('first abort', 'QuotaExceededError');
  const capture = journal.beginOpeningAttempt(card, 'ambiguous-after-abort', { freshAttempt: true })!;
  capture.response(0, manifest.decisions[0].expected_uci, 'expected');
  await vi.waitFor(() => expect(capture.storageError).toContain('first abort'));
  state.controls.beforePut = undefined;
  generation = 2;
  state.controls.beforeCommit = () => { generation = 3; return undefined; };
  await journal.recoverOpeningEvidence().catch(() => undefined);
  expect(state.stores.opening_attempts.has('ambiguous-after-abort')).toBe(true);
  state.controls.beforeCommit = undefined;
  // Model another tab acknowledging and removing the committed journal.
  state.stores.opening_attempts.clear(); state.stores.opening_events.clear();
  await journal.recoverOpeningEvidence().catch(() => undefined);
  expect(capture.rebindHeld).toBe(true);
  expect(capture.uncommittedEvents.size).toBe(1);
  expect(state.stores.opening_attempts.size).toBe(0);
});

it('FE25 a denied fresh lock leaves an existing journal untouched', async () => {
  const { openingEvidenceStorage } = await import('../fixtures/opening-evidence-storage');
  const state = await openingEvidenceStorage();
  vi.stubGlobal('navigator', { onLine: true, locks: { request: async (_name: string, _options: unknown, callback: (lock: null) => void) => callback(null) } });
  const journal = await import('../../app/lib/opening-evidence-journal');
  const { mapQueueCardToPracticeCard } = await import('../../app/domain/adapters/practice-card-adapters');
  const card = mapQueueCardToPracticeCard({ id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
    start_fen: manifest.decisions[0].fen, moves: ['e2e4','e7e5','g1f3','b8c6','f1b5'], content_type: 'opening',
    repertoire_name: 'Held', repertoire_source: 'PGN', opening_decision_manifest: manifest });
  const saved = { attempt_id: 'foreign-existing', owner_session_id: 'another-session', final_sequence: 4, delivery_state: 'pending' };
  state.stores.opening_attempts.set('foreign-existing', structuredClone(saved));
  const capture = journal.beginOpeningAttempt(card, 'foreign-existing', { freshAttempt: true })!;
  capture.response(0, manifest.decisions[0].expected_uci, 'expected');
  await vi.waitFor(() => expect(capture.storageError).toContain('ownership is unavailable'));
  expect(state.stores.opening_attempts.get('foreign-existing')).toEqual(saved);
  expect(state.stores.opening_events.size).toBe(0);
  expect(capture.uncommittedEvents.size).toBe(1);
});
