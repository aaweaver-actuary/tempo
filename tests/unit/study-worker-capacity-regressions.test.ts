// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import type { StudyTask } from "../../app/lib/study-computation";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("study_worker_abort_settles_the_caller_without_sending_a_capacity_release_message", async () => {
  vi.resetModules();
  class CapacityWorker {
    static instance: CapacityWorker;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    onmessageerror: (() => void) | null = null;
    posted: Array<{ id: number; task: StudyTask }> = [];
    terminate = vi.fn();
    constructor() { CapacityWorker.instance = this; }
    postMessage(message: { id: number; task: StudyTask }) { this.posted.push(message); }
  }
  vi.stubGlobal("Worker", CapacityWorker);
  const { runStudyTask } = await import("../../app/lib/background-study");
  const controller = new AbortController();
  const indexing = runStudyTask({ kind: "repertoireAlternatives", generation: 1, lines: [] }, controller.signal);
  const worker = CapacityWorker.instance;
  const indexId = worker.posted[0].id;
  worker.onmessage!({ data: { id: indexId, state: "running" } } as MessageEvent);
  controller.abort();
  await expect(indexing).rejects.toMatchObject({ name: "AbortError" });
  expect(worker.posted).toHaveLength(1);
  expect(worker.terminate).not.toHaveBeenCalled();
  const foreground = runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } });
  let foregroundSettled = false;
  void foreground.then(() => { foregroundSettled = true; });
  worker.onmessage!({ data: { id: indexId, result: { obsolete: true } } } as MessageEvent);
  await Promise.resolve();
  expect(foregroundSettled).toBe(false);
  const foregroundId = worker.posted[1].id;
  worker.onmessage!({ data: { id: foregroundId, result: [] } } as MessageEvent);
  await expect(foreground).resolves.toEqual([]);
});
