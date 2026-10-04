import { webcrypto } from "node:crypto";
import { afterEach, expect, it, vi } from "vitest";
import manifestFixture from "../fixtures/opening-evidence-manifest.json";
import { openingDecisionManifestSchema } from "../../app/domain/opening-evidence";
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
  const originalDelivery = { checkpoint: structuredClone(checkpoint), operationKey: "opening-checkpoint:frozen-key" };
  const attempts = new Map([[header.attempt_id, { ...header, owner_session_id: "previous-browser",
    final_sequence: 1, delivery_state: complete ? "idle" : "pending",
    terminal: { ...header.terminal!, state: complete ? "complete" : "partial" },
    delivery: complete ? undefined : originalDelivery }]]);
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
  const storage = await import("../../app/lib/offline-training-storage");
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
    if (init?.method === "POST") return Response.json({ operation_id: originalDelivery.operationKey }, { status: 202 });
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
  expect(requests).toHaveLength(pendingFirst ? 4 : 2);
  for (const [index, request] of requests.entries()) {
    expect(new Headers(request.init?.headers).get("X-Tempo-Work-Class")).toBe("background");
    if (index % 2 === 0) {
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
    return init?.method === "POST" ? Response.json({ operation_id: "review-key" }, { status: 202 })
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
    if (init?.method === "POST") return Promise.resolve(Response.json({ operation_id: originalDelivery.operationKey }, { status: postStatus }));
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
    return Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 });
  }));
  await journal.flushOpeningEvidence();
  const posts = requests.filter(request => request.init?.method === "POST");
  expect(posts).toHaveLength(2);
  expect(posts.map(request => new Headers(request.init?.headers).get("Idempotency-Key"))).toEqual([originalDelivery.operationKey, originalDelivery.operationKey]);
  expect(posts.map(request => request.init?.body)).toEqual([JSON.stringify(originalDelivery.checkpoint), JSON.stringify(originalDelivery.checkpoint)]);
  expect(attempts.size).toBe(0); expect(savedEvents.size).toBe(0);
});

it.each(["immediate", "deferred"])("AS-15 immediate and deferred durable checkpoint failure both quarantine and advance the evidence queue (%s)", async timing => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const nextCheckpoint = { ...structuredClone(checkpoint), attempt_id: "next-valid-attempt" };
  attempts.set(nextCheckpoint.attempt_id, { ...attempts.get(checkpoint.attempt_id)!, attempt_id: nextCheckpoint.attempt_id,
    delivery: { checkpoint: nextCheckpoint, operationKey: "opening-checkpoint:next-key" } });
  savedEvents.set(JSON.stringify([nextCheckpoint.attempt_id, 1]), { ...nextCheckpoint.events[0], attempt_id: nextCheckpoint.attempt_id });
  const requests: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (init?.method !== "POST") return Response.json({ state: "failed", error: { message: "Durable handler failed" } });
    const body = JSON.parse(init.body as string) as OpeningEvidenceCheckpoint;
    if (body.attempt_id === checkpoint.attempt_id) return Response.json({ operation_id: originalDelivery.operationKey }, { status: timing === "immediate" ? 500 : 202 });
    return Response.json({ persisted: true, attempt_id: body.attempt_id, received_sequences: [1], contiguous_sequence: 1 });
  }));
  await journal.flushOpeningEvidence();
  expect(requests).toHaveLength(3);
  expect(requests[1].url).toMatch(new RegExp(`/operations/${encodeURIComponent(originalDelivery.operationKey)}$`));
  expect(requests.every(request => new Headers(request.init?.headers).get("X-Tempo-Work-Class") === "background")).toBe(true);
  expect(new Headers(requests[0].init?.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
  expect(JSON.parse(requests[0].init!.body as string)).toEqual(originalDelivery.checkpoint);
  expect(JSON.parse(requests[2].init!.body as string).attempt_id).toBe(nextCheckpoint.attempt_id);
  expect(attempts.get(checkpoint.attempt_id)).toMatchObject({ delivery_state: "rejected", rejection: "Durable handler failed", delivery: originalDelivery });
  expect(attempts.has(nextCheckpoint.attempt_id)).toBe(false);
  expect([...savedEvents.values()]).toEqual([{ ...checkpoint.events[0], attempt_id: checkpoint.attempt_id }]);
});

it.each(["unknown", "pending", "retrying", "blocked", "missing", "network"])("AS-15 ambiguous checkpoint HTTP failure retains frozen delivery for unchanged retry (%s)", async state => {
  const { journal, attempts, savedEvents, originalDelivery } = await savedJournal();
  const requests: { url: string; init?: RequestInit }[] = [];
  let retry = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init });
    if (retry) return Response.json({ persisted: true, attempt_id: checkpoint.attempt_id, received_sequences: [1], contiguous_sequence: 1 });
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
  expect(requests[2].init?.body).toBe(requests[0].init?.body);
  expect(new Headers(requests[2].init?.headers).get("Idempotency-Key")).toBe(originalDelivery.operationKey);
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
  const fetching = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () =>
    Response.json({ state: "complete", response: { persisted: true } }));
  vi.stubGlobal("fetch", fetching);
  const { confirmOperationResponse } = await import("../../app/lib/operation-status");
  await confirmOperationResponse(Response.json({ operation_id: "foreground-key" }, { status: 202 }), { signal: controller.signal });
  expect(fetching).toHaveBeenCalledOnce();
  const [, init] = fetching.mock.calls[0];
  expect(init?.signal).toBe(controller.signal);
  expect(new Headers(init?.headers).has("X-Tempo-Work-Class")).toBe(false);
});
