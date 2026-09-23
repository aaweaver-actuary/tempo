import { computeStudyTask, type StudyTask } from "./study-computation";
import { studyReplySchema } from "../domain/schemas";
import { parseData, reportDataDiagnostic } from "./validated-data";
import { updateBrowserActivity } from "./browser-activity";

let worker: Worker | undefined;
let nextId = 0;
const pending = new Map<
  number,
  { title: string; resolve: (value: unknown) => void; reject: (error: Error) => void }
>();

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
          resolve(computeStudyTask(task) as T);
          updateBrowserActivity(activityId, title, "complete", "Finished");
        } catch (error) {
          updateBrowserActivity(activityId, title, "failed", "Failed", String(error));
          reject(error);
        }
      }, 0);
    });
  worker ??= new Worker(new URL("./study.worker.ts", import.meta.url), {
    type: "module",
  });
  worker.onmessage = ({ data: raw }) => {
    let data;
    try {
      data = parseData(studyReplySchema, raw, "study worker response");
    } catch (error) {
      for (const [requestId, request] of pending) {
        updateBrowserActivity(`study:${requestId}`, request.title, "failed", "Failed", String(error));
        request.reject(
          error instanceof Error ? error : new Error(String(error)),
        );
      }
      pending.clear();
      return;
    }
    const request = pending.get(data.id);
    if (!request) return;
    if (data.state === "running") {
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
    if (data.error) {
      updateBrowserActivity(`study:${data.id}`, request.title, "failed", "Failed", data.error);
      request.reject(new Error(data.error));
    } else {
      updateBrowserActivity(`study:${data.id}`, request.title, "complete", "Finished");
      request.resolve(data.result);
    }
  };
  worker.onerror = () => {
    for (const [requestId, request] of pending)
      updateBrowserActivity(`study:${requestId}`, request.title, "failed", "Failed", "Study worker failed");
    for (const request of pending.values())
      request.reject(
        new Error("Background study worker failed. Reload Tempo to retry."),
      );
    pending.clear();
    worker?.terminate();
    worker = undefined;
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
      resolve: (value) => {
        signal?.removeEventListener("abort", cancel);
        resolve(value as T);
      },
      reject: (error) => {
        signal?.removeEventListener("abort", cancel);
        reject(error);
      },
    });
    worker!.postMessage({ id, task });
  });
}
