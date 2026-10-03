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
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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
});
