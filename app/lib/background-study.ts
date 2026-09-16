import { computeStudyTask, type StudyTask } from "./study-computation";

let worker: Worker | undefined;
let nextId = 0;
const pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>();

export function runStudyTask<T>(task: StudyTask): Promise<T> {
  // Test/server environments do not have workers; still yield before computing.
  if (typeof Worker === "undefined") return new Promise<T>((resolve, reject) => {
    setTimeout(() => { try { resolve(computeStudyTask(task) as T); } catch (error) { reject(error); } }, 0);
  });
  worker ??= new Worker(new URL("./study.worker.ts", import.meta.url), { type: "module" });
  worker.onmessage = ({ data }) => {
    const request = pending.get(data.id);
    pending.delete(data.id);
    if (data.error) request?.reject(new Error(data.error));
    else request?.resolve(data.result);
  };
  worker.onerror = () => {
    for (const request of pending.values()) request.reject(new Error("Background study worker failed. Reload Tempo to retry."));
    pending.clear(); worker?.terminate(); worker = undefined;
  };
  return new Promise<T>((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve: value => resolve(value as T), reject });
    worker!.postMessage({ id, task });
  });
}
