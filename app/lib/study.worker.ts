import { createStudyPositionStore } from "./study-position-store";
import { studyRequestSchema } from "../domain/schemas";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  parseData,
} from "./validated-data";

const runStudyTaskInWorker = createStudyPositionStore();

self.onmessage = ({ data: raw }: MessageEvent<unknown>) => {
  const data = parseData(studyRequestSchema, raw, "study worker request");
  clearDataDiagnostics();
  self.postMessage({ id: data.id, state: "running" });
  const computeStartedAt = performance.now();
  try {
    const result = runStudyTaskInWorker(data.task);
    self.postMessage({
      id: data.id,
      result,
      computeMs: performance.now() - computeStartedAt,
      diagnostics: dataDiagnostics(),
    });
  } catch (error) {
    self.postMessage({
      id: data.id,
      error: error instanceof Error ? error.message : String(error),
      computeMs: performance.now() - computeStartedAt,
      diagnostics: dataDiagnostics(),
    });
  }
};
