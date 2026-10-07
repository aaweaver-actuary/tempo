import type { StudyTask } from "./study-computation";
import { createStudyPositionStore } from "./study-position-store";
import { studyReplySchema } from "../domain/schemas";
import { parseData, reportDataDiagnostic } from "./validated-data";
import { updateBrowserActivity } from "./browser-activity";
import { reportDebugError } from "./debug-reporting";
import { recordTempoDuration } from "./performance";

const runStudyTaskLocally = createStudyPositionStore();
let worker: Worker | undefined;
let nextId = 0;
const pending = new Map<
  number,
  { title: string; queuedAt: number; startedAt?: number; resolve: (value: unknown) => void; reject: (error: Error) => void }
>();

function failStudyRequests(cause?: unknown): void {
  for (const [requestId, request] of pending) {
    updateBrowserActivity(`study:${requestId}`, request.title, "failed", "Failed", "Study worker failed");
    request.reject(new Error("Study worker failed. Reopen Tempo while connected to retry.", { cause }));
  }
  pending.clear();
  worker?.terminate();
  worker = undefined;
}

export function runStudyTask<T>(
  task: StudyTask,
  signal?: AbortSignal,
): Promise<T> {
  const id = ++nextId;
  const activityId = `study:${id}`;
  const title = `Preparing ${task.kind === "workspace" ? "workspace data" : task.kind === "deck" ? "tactics" : task.kind}`;
  updateBrowserActivity(activityId, title, "queued", "Waiting for study worker");
  if (signal?.aborted) {
    updateBrowserActivity(activityId, title, "complete", "Cancelled");
    return Promise.reject(new DOMException("Cancelled", "AbortError"));
  }
  // Test/server environments do not have workers; still yield before computing.
  if (typeof Worker === "undefined")
    return new Promise<T>((resolve, reject) => {
      setTimeout(() => {
        updateBrowserActivity(activityId, title, "running", "Computing");
        if (signal?.aborted) {
          updateBrowserActivity(activityId, title, "complete", "Cancelled");
          reject(new DOMException("Cancelled", "AbortError"));
          return;
        }
        try {
          resolve(runStudyTaskLocally(task) as T);
          updateBrowserActivity(activityId, title, "complete", "Finished");
        } catch (error) {
          updateBrowserActivity(activityId, title, "failed", "Failed", String(error));
          reject(error);
        }
      }, 0);
    });
  try {
    worker ??= new Worker(new URL("./study.worker.ts", import.meta.url), { type: "module" });
  } catch (error) {
    reportDebugError(error, { kind: "uncaught-exception", source: "study-worker" });
    updateBrowserActivity(activityId, title, "failed", "Failed", String(error));
    return Promise.reject(new Error("Study worker failed. Reopen Tempo while connected to retry.", { cause: error }));
  }
  worker.onmessage = ({ data: raw }) => {
    let data;
    try {
      data = parseData(studyReplySchema, raw, "study worker response", undefined, "study-worker");
    } catch (error) {
      failStudyRequests(error);
      return;
    }
    const request = pending.get(data.id);
    if (!request) return;
    if (data.state === "running") {
      request.startedAt = performance.now();
      recordTempoDuration("study-worker-queue", request.startedAt - request.queuedAt);
      updateBrowserActivity(`study:${data.id}`, request.title, "running", "Computing");
      return;
    }
    for (const issue of data.diagnostics ?? [])
      reportDataDiagnostic(
        issue.source,
        issue.raw,
        issue.message,
        issue.recordId,
      );
    pending.delete(data.id);
    if (data.computeMs !== undefined)
      recordTempoDuration("study-worker-compute", data.computeMs);
    recordTempoDuration("study-worker-roundtrip", performance.now() - request.queuedAt);
    if (data.error) {
      updateBrowserActivity(`study:${data.id}`, request.title, "failed", "Failed", data.error);
      request.reject(new Error(data.error));
    } else {
      updateBrowserActivity(`study:${data.id}`, request.title, "complete", "Finished");
      request.resolve(data.result);
    }
  };
  worker.onerror = (event) => {
    reportDebugError(event.message || "Study worker failed to load or run", {
      kind: "uncaught-exception", source: "study-worker",
      script: event.filename, line: event.lineno || undefined,
      column: event.colno || undefined,
    });
    failStudyRequests(event);
  };
  worker.onmessageerror = () => {
    const error = new Error("Study worker response could not be decoded");
    reportDebugError(error, { kind: "uncaught-exception", source: "study-worker" });
    failStudyRequests(error);
  };
  return new Promise<T>((resolve, reject) => {
    const cancel = () => {
      pending.delete(id);
      updateBrowserActivity(activityId, title, "complete", "Cancelled");
      reject(new DOMException("Cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", cancel, { once: true });
    pending.set(id, {
      title,
      queuedAt: performance.now(),
      resolve: (value) => {
        signal?.removeEventListener("abort", cancel);
        resolve(value as T);
      },
      reject: (error) => {
        signal?.removeEventListener("abort", cancel);
        reject(error);
      },
    });
    try { worker!.postMessage({ id, task }); }
    catch (error) {
      reportDebugError(error, { kind: "uncaught-exception", source: "study-worker" });
      failStudyRequests(error);
    }
  });
}

type MatchTask = Extract<StudyTask, { kind: "findPositionMatches" }>;
type CoalescedRequest = {
  task: MatchTask;
  enqueuedAt: number;
  signal?: AbortSignal;
  onAbort: () => void;
  settled: boolean;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
};
type MatchSlot = { active?: CoalescedRequest; queued?: CoalescedRequest };
const matchSlots = new Map<string, MatchSlot>();

// Keep one computation in flight per repertoire. A synchronous worker computation
// cannot be interrupted, but requests waiting behind it can be replaced.
export function runCoalescedStudyMatch<T>(task: MatchTask, signal?: AbortSignal): Promise<T> {
  if (signal?.aborted) return Promise.reject(new DOMException("Cancelled", "AbortError"));
  const key = task.repertoireId;
  const slot = matchSlots.get(key) ?? {};
  matchSlots.set(key, slot);
  return new Promise<T>((resolve, reject) => {
    const request: CoalescedRequest = {
      task,
      enqueuedAt: performance.now(),
      signal,
      settled: false,
      resolve: (value) => resolve(value as T),
      reject,
      onAbort: () => {
        if (slot.queued === request) slot.queued = undefined;
        settleError(request, new DOMException("Cancelled", "AbortError"));
        if (!slot.active && !slot.queued) matchSlots.delete(key);
      },
    };
    signal?.addEventListener("abort", request.onAbort, { once: true });
    if (slot.active) {
      if (slot.queued) {
        const superseded = slot.queued;
        slot.queued = undefined;
        settleError(superseded, new DOMException("Superseded", "AbortError"));
      }
      slot.queued = request;
    } else {
      startMatch(request, slot, key);
    }
  });
}

function settleError(request: CoalescedRequest, error: Error) {
  if (request.settled) return;
  request.settled = true;
  request.signal?.removeEventListener("abort", request.onAbort);
  request.reject(error);
}

function advanceMatchSlot(slot: MatchSlot, key: string) {
  slot.active = undefined;
  const next = slot.queued;
  slot.queued = undefined;
  if (next) startMatch(next, slot, key);
  else matchSlots.delete(key);
}

function startMatch(request: CoalescedRequest, slot: MatchSlot, key: string) {
  slot.active = request;
  recordTempoDuration("study-match-coalesced-wait", performance.now() - request.enqueuedAt);
  let work: Promise<unknown>;
  try {
    work = runStudyTask(request.task);
  } catch (error) {
    settleError(request, error instanceof Error ? error : new Error(String(error)));
    advanceMatchSlot(slot, key);
    return;
  }
  void work.then(
    (value) => {
      if (request.settled) return;
      request.settled = true;
      request.signal?.removeEventListener("abort", request.onAbort);
      request.resolve(value);
    },
    (error) => settleError(request, error instanceof Error ? error : new Error(String(error))),
  ).finally(() => advanceMatchSlot(slot, key));
}
