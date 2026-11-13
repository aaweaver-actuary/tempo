import type { StudyTask, StudyTaskResult } from "./study-computation";
import { computeEssentialStudyTask } from "./essential-study-computation";
import { isBulkStudyTask, runBulkStudyTask } from './bulk-study';
import { decodeStudyResult } from "./study-result-decoder";
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
  { task: StudyTask; title: string; queuedAt: number; startedAt?: number; shouldPublishDiagnostics?: () => boolean; resolve: (value: unknown) => void; reject: (error: Error) => void }
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

export function runStudyTask<Task extends StudyTask>(
  task: Task,
  signal?: AbortSignal,
  shouldPublishDiagnostics?: () => boolean,
): Promise<StudyTaskResult<Task>> {
  if (typeof Worker !== 'undefined' && isBulkStudyTask(task))
    return runBulkStudyTask(task, signal, shouldPublishDiagnostics);
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
    return new Promise<StudyTaskResult<Task>>((resolve, reject) => {
      setTimeout(async () => {
        updateBrowserActivity(activityId, title, "running", "Computing");
        if (signal?.aborted) {
          updateBrowserActivity(activityId, title, "complete", "Cancelled");
          reject(new DOMException("Cancelled", "AbortError"));
          return;
        }
        try {
          const optional = task.kind !== "workspace" && isBulkStudyTask(task)
            ? await import("./optional-study-computation") : undefined;
          if (signal?.aborted) {
            updateBrowserActivity(activityId, title, "complete", "Cancelled");
            reject(new DOMException("Cancelled", "AbortError"));
            return;
          }
          const effects = { reportIssue: (issue: import("../domain/validation").ValidationIssue) => reportDataDiagnostic(issue.source, issue.raw, issue.message, issue.recordId),
            reportError: reportDebugError, shouldPublishDiagnostics };
          resolve((task.kind === "workspace"
            ? validateWorkspacePayload(task.url, task.payload, shouldPublishDiagnostics, effects)
            : optional ? optional.computeOptionalStudyTask(task, effects)
              : computeEssentialStudyTask(task, effects)) as StudyTaskResult<Task>);
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
    if (data.error !== undefined) {
      updateBrowserActivity(`study:${data.id}`, request.title, "failed", "Failed", data.error);
      request.reject(new Error(data.error));
    } else {
      try {
        const result = decodeStudyResult(request.task, data.result, request.shouldPublishDiagnostics, { reportIssue: issue => reportDataDiagnostic(issue.source, issue.raw, issue.message, issue.recordId), reportError: reportDebugError, shouldPublishDiagnostics: request.shouldPublishDiagnostics });
        updateBrowserActivity(`study:${data.id}`, request.title, "complete", "Finished");
        request.resolve(result);
      } catch (error) {
        updateBrowserActivity(`study:${data.id}`, request.title, "failed", "Failed", "Invalid worker result");
        request.reject(new Error("Study worker returned invalid data", { cause: error }));
      }
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
  return new Promise<StudyTaskResult<Task>>((resolve, reject) => {
    const cancel = () => {
      pending.delete(id);
      updateBrowserActivity(activityId, title, "complete", "Cancelled");
      reject(new DOMException("Cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", cancel, { once: true });
    pending.set(id, {
      task,
      title,
      queuedAt: performance.now(),
      shouldPublishDiagnostics,
      resolve: (value) => {
        signal?.removeEventListener("abort", cancel);
        resolve(value as StudyTaskResult<Task>);
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
