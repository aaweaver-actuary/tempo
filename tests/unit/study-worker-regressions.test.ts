// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { runStudyTask } from "../../app/lib/background-study";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { tempoPerformanceTimings } from "../../app/lib/performance";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearDebugErrors();
});

it("schema-invalid study worker reply rejects all callers and replaces the worker before retry", async () => {
  vi.resetModules();
  const { runStudyTask: runTask } = await import("../../app/lib/background-study");
  const { debugErrors: currentDebugErrors } = await import("../../app/lib/debug-reporting");
  const { browserActivitySnapshot } = await import("../../app/lib/browser-activity");
  const workers: ProtocolWorker[] = [];
  class ProtocolWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    terminate = vi.fn();
    postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal("Worker", ProtocolWorker);
  const firstController = new AbortController();
  const secondController = new AbortController();
  const firstCleanup = vi.spyOn(firstController.signal, "removeEventListener");
  const secondCleanup = vi.spyOn(secondController.signal, "removeEventListener");
  const first = runTask({ kind: "queue", payload: { cards: [], count: 0 } }, firstController.signal);
  const second = runTask({ kind: "workspace", url: "/api/repertoire/lines", payload: [] }, secondController.signal);
  const settled = Promise.allSettled([first, second]);
  const staleMessage = workers[0].onmessage!;
  const staleError = workers[0].onerror!;
  const staleMessageError = workers[0].onmessageerror!;
  const invalidReply = structuredClone({ id: "stale-worker-protocol", result: [] });
  workers[0].onmessage!({ data: invalidReply } as MessageEvent);
  const failures = await settled;
  expect(failures).toHaveLength(2);
  for (const failure of failures) {
    expect(failure.status).toBe("rejected");
    if (failure.status === "rejected") expect(String(failure.reason)).toContain("Reopen Tempo while connected");
  }
  expect(firstCleanup).toHaveBeenCalledWith("abort", expect.any(Function));
  expect(secondCleanup).toHaveBeenCalledWith("abort", expect.any(Function));
  expect(browserActivitySnapshot().filter(activity => activity.id.startsWith("study:")).map(activity => activity.state)).toEqual(["failed", "failed"]);
  expect(currentDebugErrors()).toHaveLength(1);
  expect(currentDebugErrors()[0]).toMatchObject({ kind: "data-validation", context: { source: "study-worker" },
    message: expect.stringContaining("Invalid study worker response") });
  expect(workers[0].terminate).toHaveBeenCalledOnce();
  firstController.abort(); secondController.abort();
  expect(browserActivitySnapshot().every(activity => activity.state === "failed")).toBe(true);
  const retry = runTask({ kind: "queue", payload: { cards: [], count: 0 } });
  expect(workers).toHaveLength(2);
  const requestId = workers[1].postMessage.mock.calls[0][0].id;
  let retrySettled = false;
  void retry.then(() => { retrySettled = true; }, () => { retrySettled = true; });
  staleMessage({ data: { id: requestId, state: "running" } } as MessageEvent);
  staleMessage({ data: { id: requestId, result: ["stale-worker"] } } as MessageEvent);
  staleMessage({ data: invalidReply } as MessageEvent);
  staleError({ message: "Late failure from terminated worker" } as ErrorEvent);
  staleMessageError();
  await Promise.resolve();
  expect(retrySettled).toBe(false);
  expect(workers[1].terminate).not.toHaveBeenCalled();
  expect(currentDebugErrors()).toHaveLength(1);
  expect(workers[0].onmessage).toBeNull();
  expect(workers[0].onerror).toBeNull();
  expect(workers[0].onmessageerror).toBeNull();
  workers[1].onmessage!({ data: { id: requestId, result: ["fresh-worker"] } } as MessageEvent);
  await expect(retry).resolves.toEqual(["fresh-worker"]);
});

it("phone worker construction failure rejects asynchronously with actionable diagnostics", async () => {
  vi.stubGlobal("Worker", class { constructor() { throw new DOMException("Worker denied", "SecurityError"); } });
  await expect(runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } }))
    .rejects.toThrow("Reopen Tempo while connected");
  expect(debugErrors().at(-1)?.context.source).toBe("study-worker");
});

it("iPhone study worker startup failure is actionable and records the safe script path", async () => {
  class FailingWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    postMessage() {
      this.onerror?.({
        message: "Script error.", filename: "https://tempo.example/assets/study.worker.js?secret=token",
        lineno: 3, colno: 2,
      } as ErrorEvent);
    }
    terminate() {}
  }
  vi.stubGlobal("Worker", FailingWorker);
  await expect(runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } }))
    .rejects.toThrow("Reopen Tempo while connected");
  expect(debugErrors().at(-1)?.context).toMatchObject({
    source: "study-worker", scriptPath: "/assets/study.worker.js", line: 3, column: 2,
  });
});

it("study worker reports queue compute and roundtrip durations", async () => {
  class TimingWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    postMessage(request: { id: number }) {
      this.onmessage?.({ data: { id: request.id, state: "running" } } as MessageEvent);
      this.onmessage?.({ data: { id: request.id, result: [], computeMs: 7 } } as MessageEvent);
    }
    terminate() {}
  }
  vi.stubGlobal("Worker", TimingWorker);
  const before = tempoPerformanceTimings().length;
  await expect(runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } })).resolves.toEqual([]);
  const timings = tempoPerformanceTimings().slice(before);
  expect(timings.map(({ operation }) => operation)).toEqual([
    "study-worker-queue", "study-worker-compute", "study-worker-roundtrip",
  ]);
  expect(timings[1].duration).toBe(7);
  expect(timings.every(({ duration }) => duration >= 0)).toBe(true);
});

it("phone worker message decoding failure releases every caller and permits a fresh worker", async () => {
  vi.resetModules();
  const { runStudyTask: runTask } = await import("../../app/lib/background-study");
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    terminate = vi.fn();
    postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal("Worker", ControlledWorker);
  const first = runTask({ kind: "queue", payload: { cards: [], count: 0 } });
  const second = runTask({ kind: "workspace", url: "/api/repertoire/lines", payload: [] });
  const failures = Promise.all([expect(first).rejects.toThrow("Reopen Tempo while connected"),
    expect(second).rejects.toThrow("Reopen Tempo while connected")]);
  workers[0].onmessageerror!();
  await failures;
  expect(workers[0].terminate).toHaveBeenCalledOnce();
  const retry = runTask({ kind: "queue", payload: { cards: [], count: 0 } });
  expect(workers).toHaveLength(2);
  const id = workers[1].postMessage.mock.calls[0][0].id;
  workers[1].onmessage!({ data: { id, result: [] } } as MessageEvent);
  await expect(retry).resolves.toEqual([]);
});
