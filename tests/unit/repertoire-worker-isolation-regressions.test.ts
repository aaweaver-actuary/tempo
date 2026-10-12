// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("issue190_bulk_cancellation_terminates_only_bulk_worker_and_preserves_foreground_capacity", async () => {
  vi.resetModules();
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    terminate = vi.fn();
    postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const { runStudyTask } = await import("../../app/lib/background-study");
  const cancellation = new AbortController();
  const bulk = runStudyTask({ kind: "repertoireAlternatives", generation: 1, lines: [] }, cancellation.signal);
  const cancelled = bulk.catch(error => error);
  const foreground = runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
  const workerCount = workers.length;
  cancellation.abort();
  expect(await cancelled).toMatchObject({ name: "AbortError" });
  expect(workerCount).toBe(2);
  const bulkWorker = workers[0];
  const foregroundWorker = workers[1];

  expect(bulkWorker.terminate).toHaveBeenCalledOnce();
  expect(foregroundWorker.terminate).not.toHaveBeenCalled();
  const requestId = foregroundWorker.postMessage.mock.calls[0][0].id;
  foregroundWorker.onmessage!({ data: { id: requestId, result: [] } } as MessageEvent);
  await expect(foreground).resolves.toEqual([]);
});

it("issue190_three_invalidations_reject_obsolete_callbacks_and_allow_fresh_retry", async () => {
  vi.resetModules();
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    terminate = vi.fn();
    postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const { runStudyTask } = await import("../../app/lib/background-study");
  const obsolete = [];
  const callbacks = [];
  for (let generation = 1; generation <= 3; generation++) {
    obsolete.push(runStudyTask({ kind: "repertoirePreparation", generation, payload: { lines: [] } }).catch(error => error));
    callbacks.push(workers.at(-1)!.onmessage!);
  }
  const latest = runStudyTask({ kind: "repertoirePreparation", generation: 4, payload: { lines: [] } });
  const latestWorker = workers.at(-1)!;
  const latestId = latestWorker.postMessage.mock.calls[0][0].id;
  let resolved = false;
  void latest.then(() => { resolved = true; });
  for (const obsoleteCallback of callbacks) obsoleteCallback({ data: { id: latestId, result: { generation: 4, positions: [], movesByRepertoire: new Map() } } } as MessageEvent);
  await Promise.resolve();
  expect(resolved).toBe(false);
  latestWorker.onmessage!({ data: { id: latestId, result: { generation: 4, positions: [], movesByRepertoire: new Map() } } } as MessageEvent);
  await expect(latest).resolves.toMatchObject({ generation: 4 });
  for (const result of await Promise.all(obsolete)) expect(result).toMatchObject({ name: "AbortError" });
  expect(workers.every(worker => worker.terminate.mock.calls.length === 1)).toBe(true);
});

it.each(["runtime", "decoding", "protocol", "generation"])("issue190_bulk_failure_is_actionable_and_retry_is_independent (%s)", async failure => {
  vi.resetModules();
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    terminate = vi.fn();
    postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const { runStudyTask } = await import("../../app/lib/background-study");
  const failed = runStudyTask({ kind: "repertoirePreparation", generation: 1, payload: { lines: [] } });
  const rejected = failed.catch(error => error);
  const failedWorker = workers[0];
  const requestId = failedWorker.postMessage.mock.calls[0][0].id;
  if (failure === "runtime") failedWorker.onerror!({ message: "Worker crashed" } as ErrorEvent);
  else if (failure === "decoding") failedWorker.onmessageerror!();
  else failedWorker.onmessage!({ data: failure === "protocol" ? { id: "invalid" }
    : { id: requestId, result: { generation: 0, positions: [], movesByRepertoire: new Map() } } } as MessageEvent);
  expect(String(await rejected)).toContain("disable repertoire preparation in Settings");
  expect(failedWorker.terminate).toHaveBeenCalledOnce();
  const retry = runStudyTask({ kind: "repertoirePreparation", generation: 2, payload: { lines: [] } });
  const retryWorker = workers[1];
  retryWorker.onmessage!({ data: { id: retryWorker.postMessage.mock.calls[0][0].id,
    result: { generation: 2, positions: [], movesByRepertoire: new Map() } } } as MessageEvent);
  await expect(retry).resolves.toMatchObject({ generation: 2 });
});
