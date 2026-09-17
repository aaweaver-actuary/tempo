import { computeStudyTask, type StudyTask } from "./study-computation";
import { studyReplySchema } from "../domain/schemas";
import { parseData, reportDataDiagnostic } from "./validated-data";

let worker: Worker | undefined;
let nextId = 0;
const pending = new Map<
  number,
  { resolve: (value: unknown) => void; reject: (error: Error) => void }
>();

export function runStudyTask<T>(
  task: StudyTask,
  signal?: AbortSignal,
): Promise<T> {
  if (signal?.aborted)
    return Promise.reject(new DOMException("Cancelled", "AbortError"));
  // Test/server environments do not have workers; still yield before computing.
  if (typeof Worker === "undefined")
    return new Promise<T>((resolve, reject) => {
      setTimeout(() => {
        if (signal?.aborted) {
          reject(new DOMException("Cancelled", "AbortError"));
          return;
        }
        try {
          resolve(computeStudyTask(task) as T);
        } catch (error) {
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
      for (const request of pending.values())
        request.reject(
          error instanceof Error ? error : new Error(String(error)),
        );
      pending.clear();
      return;
    }
    const request = pending.get(data.id);
    if (!request) return;
    for (const issue of data.diagnostics ?? [])
      reportDataDiagnostic(
        issue.source,
        issue.raw,
        issue.message,
        issue.recordId,
      );
    pending.delete(data.id);
    if (data.error) request?.reject(new Error(data.error));
    else request?.resolve(data.result);
  };
  worker.onerror = () => {
    for (const request of pending.values())
      request.reject(
        new Error("Background study worker failed. Reload Tempo to retry."),
      );
    pending.clear();
    worker?.terminate();
    worker = undefined;
  };
  return new Promise<T>((resolve, reject) => {
    const id = ++nextId;
    const cancel = () => {
      pending.delete(id);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    signal?.addEventListener("abort", cancel, { once: true });
    pending.set(id, {
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
