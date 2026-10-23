import { computeStudyTask, type StudyTask } from "./study-computation";
import { isBulkStudyTask, runBulkStudyTask } from './bulk-study';
import { studyReplySchema } from "../domain/schemas";
import { parseData, reportDataDiagnostic } from "./validated-data";
import { updateBrowserActivity } from "./browser-activity";
import { reportDebugError } from "./debug-reporting";
import { recordTempoDuration } from "./performance";
import { validateWorkspacePayload } from "../domain/adapters/workspace-adapters";

let worker: Worker | undefined;
let nextId = 0;
const pending = new Map<
  number,
  { title: string; queuedAt: number; startedAt?: number; shouldPublishDiagnostics?: () => boolean; resolve: (value: unknown) => void; reject: (error: Error) => void }
>();

function failStudyRequests(cause?: unknown): void {
  for (const [requestId, request] of pending) {
    updateBrowserActivity(`study:${requestId}`, request.title, "failed", "Failed", "Study worker failed");
    request.reject(new Error("Study worker failed. Reopen Tempo while connected to retry.", { cause }));
  }
  pending.clear();
  if (worker) {
    worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null;
    worker.terminate();
    worker = undefined;
  }
}

export function runStudyTask<T>(
  task: StudyTask,
  signal?: AbortSignal,
  shouldPublishDiagnostics?: () => boolean,
): Promise<T> {
  if (typeof Worker !== 'undefined' && isBulkStudyTask(task))
    return runBulkStudyTask<T>(task, signal, shouldPublishDiagnostics);
  const id = ++nextId;
  const activityId = `study:${id}`;
  const title = `Preparing ${task.kind === "workspace" ? "workspace data" : task.kind === "deck" ? "tactics" : task.kind === "repertoireAlternatives" ? "repertoire lines" : task.kind}`;
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
          resolve((task.kind === "workspace"
            ? validateWorkspacePayload(task.url, task.payload, shouldPublishDiagnostics)
            : computeStudyTask(task)) as T);
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
  const requestWorker = worker;
  requestWorker.onmessage = ({ data: raw }) => {
    if (worker !== requestWorker) return;
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
    for (const issue of data.diagnostics ?? []) {
      if (request.shouldPublishDiagnostics?.() === false) break;
      reportDataDiagnostic(
        issue.source,
        issue.raw,
        issue.message,
        issue.recordId,
      );
    }
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
  requestWorker.onerror = (event) => {
    if (worker !== requestWorker) return;
    reportDebugError(event.message || "Study worker failed to load or run", {
      kind: "uncaught-exception", source: "study-worker",
      script: event.filename, line: event.lineno || undefined,
      column: event.colno || undefined,
    });
    failStudyRequests(event);
  };
  requestWorker.onmessageerror = () => {
    if (worker !== requestWorker) return;
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
      shouldPublishDiagnostics,
      resolve: (value) => {
        signal?.removeEventListener("abort", cancel);
        resolve(value as T);
      },
      reject: (error) => {
        signal?.removeEventListener("abort", cancel);
        reject(error);
      },
    });
    try { requestWorker.postMessage({ id, task }); }
    catch (error) {
      reportDebugError(error, { kind: "uncaught-exception", source: "study-worker" });
      failStudyRequests(error);
    }
  });
}
