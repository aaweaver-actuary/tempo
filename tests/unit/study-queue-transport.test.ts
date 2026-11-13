import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createQueueTransport } from "../../app/features/study/queue-transport";

const original = "queue.issue_head:original";
const accepted = (state = "queued") => Response.json({ operation_id: original, state }, { status: 202, headers: { "Retry-After": "0.3" } });
const receipt = (state: string, extra: object = {}) => Response.json({ operation_id: original, state, ...extra });
function transport(fetcher: typeof fetch) {
  return createQueueTransport({ fetch: fetcher, authority: () => ({ baseUrl: "https://original.example", isCurrent: () => true }), timer: {
    setTimeout: (callback, delay) => globalThis.setTimeout(callback, delay), clearTimeout: handle => globalThis.clearTimeout(handle),
  } });
}
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("follows the server-issued operation through unknown and pending then rereads the authoritative queue", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(accepted("unknown"))
    .mockResolvedValueOnce(receipt("unknown")).mockResolvedValueOnce(receipt("executing"))
    .mockResolvedValueOnce(receipt("complete", { response: { cards: [{ id: "stale-command-result" }] } }))
    .mockResolvedValueOnce(Response.json({ cards: [], count: 0 }));
  const acquisition = { observedBusyHead: false };
  const result = transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, acquisition);
  await vi.advanceTimersByTimeAsync(1000);
  expect(await result).toEqual({ cards: [], count: 0 });
  expect(acquisition.observedBusyHead).toBe(true);
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
    "https://original.example/api/queue/window?limit=20&include_opening_evidence=true",
    ...Array(3).fill(`https://original.example/api/operations/${encodeURIComponent(original)}`),
    "https://original.example/api/queue/window?limit=20&include_opening_evidence=true",
  ]);
  expect(fetcher.mock.calls[4][1]?.headers).toBeUndefined();
});

it.each([{ state: "queued" }, { operation_id: "../foreign", state: "queued" }])("rejects malformed initial identity without another request: %j", async invalid => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(invalid, { status: 202 }));
  await expect(transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false })).rejects.toThrow("invalid pending");
  expect(fetcher).toHaveBeenCalledOnce();
});

it("rejects a foreign completed receipt without reading another queue", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(accepted())
    .mockResolvedValueOnce(Response.json({ operation_id: "foreign", state: "complete", response: {} }));
  const loading = transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  const failure = expect(loading).rejects.toThrow("original issuance");
  await vi.advanceTimersByTimeAsync(400); await failure;
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("treats a completed hold as a receipt and reacquires a fresh queue", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(accepted()).mockResolvedValueOnce(receipt("complete", { response: { issued: false, position_study_hold: "position_card_busy" } }))
    .mockResolvedValueOnce(Response.json({ cards: [], count: 0 }));
  const loading = transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  await vi.advanceTimersByTimeAsync(400);
  expect(await loading).toEqual({ cards: [], count: 0 }); expect(fetcher).toHaveBeenCalledTimes(3);
});

it("retries an explicit foreground wait as background observation without inventing an operation", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ code: "foreground_wait" }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ cards: [], count: 0 }));
  const loading = transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  await vi.advanceTimersByTimeAsync(300);
  expect(await loading).toEqual({ cards: [], count: 0 });
  expect(fetcher.mock.calls[1][1]?.headers).toEqual({ "X-Tempo-Work-Class": "background" });
  expect(fetcher.mock.calls.every(([url]) => !String(url).includes("/api/operations/"))).toBe(true);
});

it("aborts a pending status fetch without a cancellation request", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(accepted()).mockImplementation(() => new Promise<Response>(() => {}));
  const controller = new AbortController();
  const loading = transport(fetcher).loadTodayQueueWithRetry(controller.signal, { observedBusyHead: false });
  const failure = expect(loading).rejects.toBeDefined();
  await vi.advanceTimersByTimeAsync(400); controller.abort(new Error("superseded")); await failure;
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[1][1]?.signal.aborted).toBe(true);
  expect(fetcher.mock.calls.every(([, init]) => init?.method === undefined)).toBe(true);
});

it("bounds an unknown original operation without requesting a fresh issuance", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(accepted("unknown")).mockImplementation(async () => receipt("unknown"));
  const loading = transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  const failure = expect(loading).rejects.toThrow("still pending");
  await vi.advanceTimersByTimeAsync(6100); await failure;
  expect(fetcher).toHaveBeenCalledTimes(21);
  expect(fetcher.mock.calls.slice(1).every(([url]) => String(url).endsWith(encodeURIComponent(original)))).toBe(true);
});

it("returns a nontransient held head without treating it as a new issuance", async () => {
  const card = { id: "held", queue_entry_id: 1, start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", moves: ["e2e4"],
    content_type: "opening", repertoire_name: "Held", repertoire_source: "fixture", position_study_mode: "position",
    position_presentation: null, position_study_hold: "source_requires_repair" };
  const fetcher = vi.fn().mockResolvedValue(Response.json({ cards: [card], count: 1 }));
  expect(await transport(fetcher).loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false })).toEqual({ cards: [card], count: 1 });
  expect(fetcher).toHaveBeenCalledOnce();
});

it("fences an in-flight original operation when authority changes and uses the new authority only for a new acquisition", async () => {
  let origin = "https://first.example", generation = 1;
  const fetcher = vi.fn().mockResolvedValueOnce(accepted()).mockResolvedValueOnce(Response.json({ cards: [], count: 0 }));
  const client = createQueueTransport({ fetch: fetcher, authority: () => {
    const capturedOrigin = origin, capturedGeneration = generation;
    return { baseUrl: capturedOrigin, isCurrent: () => origin === capturedOrigin && generation === capturedGeneration };
  }, timer: { setTimeout: (callback, delay) => globalThis.setTimeout(callback, delay), clearTimeout: handle => globalThis.clearTimeout(handle) } });
  const originalLoad = client.loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  const rejected = expect(originalLoad).rejects.toMatchObject({ name: "QueueAuthorityChangedError" });
  await vi.advanceTimersByTimeAsync(0);
  origin = "https://second.example"; generation++;
  await vi.advanceTimersByTimeAsync(400); await rejected;
  expect(fetcher).toHaveBeenCalledOnce();
  expect(String(fetcher.mock.calls[0][0])).toContain("https://first.example/api/queue/window");
  expect(await client.loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false })).toEqual({ cards: [], count: 0 });
  expect(String(fetcher.mock.calls[1][0])).toContain("https://second.example/api/queue/window");
});

it("composed queue acquisition binds connection generation across connect and reconnect", async () => {
  vi.useRealTimers(); vi.resetModules();
  const { IDBFactory } = await import("fake-indexeddb");
  vi.stubGlobal("__TEMPO_STUDY__", true); vi.stubGlobal("__TEMPO_DEMO__", false); vi.stubGlobal("indexedDB", new IDBFactory());
  localStorage.clear(); sessionStorage.clear();
  const bootstrap = { schema_version: 1, workspace_id: "workspace-a", timezone: "UTC", local_date: "2026-11-13",
    settings: { study_only_mode: false, new_cards_per_day: 10, study_new_per_day: 10, tactics_new_per_day: 5,
      include_defensive_cards_in_daily_stack: true, light_first_interval_days: 7 } };
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const address = String(input);
    if (address.endsWith("/api/study/bootstrap")) return Response.json(bootstrap);
    if (address.startsWith("http://first.test") && address.includes("/api/queue/window")) return accepted();
    if (address.startsWith("http://second.test") && address.includes("/api/queue/window")) return Response.json({ cards: [], count: 0 });
    throw new Error(`Unexpected queue transport ${address}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const { fetchAndInitializeQueue } = await import("../../app/views/fetchAndInitializeQueue");
  const connection = await import("../../app/lib/study-connection");
  const storage = await import("../../database/browser/study-intent-storage");
  try {
    await connection.connectStudy("http://first.test", "workspace-a", "test-credential-12345678901234567890");
    const first = fetchAndInitializeQueue(false, { preparePhoneQueue: false, replaySavedReviews: false });
    await vi.waitFor(() => expect(fetcher.mock.calls.some(([url]) => String(url).includes("first.test/api/queue/window"))).toBe(true));
    await connection.connectStudy("http://second.test", "workspace-a", "test-credential-12345678901234567890");
    expect(await first).toBe("superseded");
    const firstOriginCalls = fetcher.mock.calls.filter(([url]) => String(url).includes("first.test"));
    expect(firstOriginCalls.filter(([url]) => String(url).includes("/api/operations/"))).toHaveLength(0);
    expect(await fetchAndInitializeQueue(false, { preparePhoneQueue: false, replaySavedReviews: false })).toBe("exhausted");
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/api/queue/window")).map(([url]) => new URL(String(url)).origin))
      .toEqual(["http://first.test", "http://second.test"]);
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("/api/operations/"))).toHaveLength(0);
  } finally { storage.closeStudyIntentStorage(); connection.disconnectStudy(); vi.unstubAllGlobals(); localStorage.clear(); sessionStorage.clear(); }
}, 15_000);

it("does not accept an old completed receipt after authority changes during its status request", async () => {
  let origin = "https://first.example", generation = 1, resolveReceipt!: (response: Response) => void;
  const delayed = new Promise<Response>(resolve => { resolveReceipt = resolve; });
  const fetcher = vi.fn().mockResolvedValueOnce(accepted()).mockReturnValueOnce(delayed);
  const client = createQueueTransport({ fetch: fetcher, authority: () => {
    const captured = origin, version = generation;
    return { baseUrl: captured, isCurrent: () => origin === captured && generation === version };
  }, timer: { setTimeout: (callback, delay) => globalThis.setTimeout(callback, delay), clearTimeout: handle => globalThis.clearTimeout(handle) } });
  const load = client.loadTodayQueueWithRetry(new AbortController().signal, { observedBusyHead: false });
  const rejected = expect(load).rejects.toMatchObject({ name: "QueueAuthorityChangedError" });
  await vi.advanceTimersByTimeAsync(400);
  expect(fetcher).toHaveBeenCalledTimes(2);
  origin = "https://second.example"; generation++;
  resolveReceipt(receipt("complete", { response: { issued: true } }));
  await rejected;
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(String(fetcher.mock.calls[1][0])).toBe(`https://first.example/api/operations/${encodeURIComponent(original)}`);
});
