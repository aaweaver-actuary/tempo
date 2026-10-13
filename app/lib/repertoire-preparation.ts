import type { StudyTask } from "./study-computation";
import type { RepertoireAlternativeSnapshot } from "./repertoire-alternative-index";
import { studyReplySchema } from "../domain/schemas";
import { parseData, reportDataDiagnostic } from "./validated-data";
import { updateBrowserActivity } from "./browser-activity";
import { recordTempoDuration } from "./performance";
import { reportDebugError } from "./debug-reporting";

type RepertoireTask = Extract<StudyTask, { kind: "repertoirePreparation" | "repertoireAlternatives" }>;
let nextBulkRequestId = 0;
let cancelActivePreparation: (() => void) | undefined;

// A disposable worker owns the complete bulk pipeline. Its termination must never
// destroy the foreground worker's retained Builder or comparison indexes.
export function runRepertoirePreparation(
  task: RepertoireTask, signal?: AbortSignal, shouldPublishDiagnostics?: () => boolean,
): Promise<RepertoireAlternativeSnapshot> {
  cancelActivePreparation?.();
  if (signal?.aborted) return Promise.reject(new DOMException("Cancelled", "AbortError"));
  const requestId = ++nextBulkRequestId;
  const activityId = `repertoire:${requestId}`;
  const title = "Preparing repertoire lines";
  const queuedAt = performance.now();
  updateBrowserActivity(activityId, title, "queued", "Waiting for repertoire worker");
  return new Promise((resolve, reject) => {
    let preparationWorker: Worker | undefined;
    let settled = false;
    const cleanup = () => {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", cancel);
      if (cancelActivePreparation === cancel) cancelActivePreparation = undefined;
      if (preparationWorker) {
        preparationWorker.onmessage = null;
        preparationWorker.onerror = null;
        preparationWorker.onmessageerror = null;
        preparationWorker.terminate();
      }
    };
    const fail = (cause: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      const error = new Error("Repertoire preparation failed. Retry loading lines or disable repertoire preparation in Settings.", { cause });
      reportDebugError(cause, { kind: "uncaught-exception", source: "repertoire-worker" });
      updateBrowserActivity(activityId, title, "failed", "Failed", error.message);
      reject(error);
    };
    const cancel = () => {
      if (settled) return;
      settled = true;
      cleanup();
      updateBrowserActivity(activityId, title, "complete", "Cancelled");
      reject(new DOMException("Cancelled", "AbortError"));
    };
    // Bound failure recovery even when a loaded worker never sends a reply.
    const timeout = setTimeout(() => fail(new Error("Repertoire worker timed out after 120 seconds")), 120_000);
    cancelActivePreparation = cancel;
    signal?.addEventListener("abort", cancel, { once: true });
    try {
      preparationWorker = new Worker(new URL("./study.worker.ts", import.meta.url), { type: "module", name: "tempo-repertoire-preparation" });
      preparationWorker.onmessage = ({ data: raw }) => {
        if (settled) return;
        try {
          const reply = parseData(studyReplySchema, raw, "repertoire worker response", undefined, "repertoire-worker");
          if (reply.id !== requestId) throw new Error("Repertoire worker returned an unrelated request");
          if (reply.state === "running") {
            recordTempoDuration("repertoire-worker-queue", performance.now() - queuedAt);
            updateBrowserActivity(activityId, title, "running", "Computing");
            return;
          }
          for (const diagnostic of reply.diagnostics ?? []) {
            if (shouldPublishDiagnostics?.() === false) break;
            reportDataDiagnostic(diagnostic.source, diagnostic.raw, diagnostic.message, diagnostic.recordId);
          }
          if (reply.error) throw new Error(reply.error);
          const snapshot = reply.result as RepertoireAlternativeSnapshot | undefined;
          if (!snapshot || snapshot.generation !== task.generation || !Array.isArray(snapshot.positions)
              || !(snapshot.movesByRepertoire instanceof Map))
            throw new Error("Repertoire worker returned an invalid or obsolete snapshot");
          settled = true;
          cleanup();
          if (reply.computeMs !== undefined) recordTempoDuration("repertoire-worker-compute", reply.computeMs);
          recordTempoDuration("repertoire-worker-roundtrip", performance.now() - queuedAt);
          updateBrowserActivity(activityId, title, "complete", "Finished");
          resolve(snapshot);
        } catch (error) { fail(error); }
      };
      preparationWorker.onerror = event => fail(new Error(event.message || "Repertoire worker failed"));
      preparationWorker.onmessageerror = () => fail(new Error("Repertoire worker response could not be decoded"));
      preparationWorker.postMessage({ id: requestId, task });
    } catch (error) { fail(error); }
  });
}
