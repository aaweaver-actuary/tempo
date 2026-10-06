// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { runStudyTask } from "../../app/lib/background-study";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";
import { tempoPerformanceTimings } from "../../app/lib/performance";

afterEach(() => {
  vi.unstubAllGlobals();
  clearDebugErrors();
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
