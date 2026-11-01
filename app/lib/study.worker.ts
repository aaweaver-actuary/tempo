import { computeStudyTask } from "./study-computation";
import { studyRequestSchema } from "../domain/schemas";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  parseData,
} from "./validated-data";

self.onmessage = ({ data: raw }: MessageEvent<unknown>) => {
  let data;
  try {
    data = parseData(studyRequestSchema, raw, "study worker request");
  } catch (error) {
    const id = typeof raw === "object" && raw !== null && "id" in raw ? raw.id : undefined;
    if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) throw error;
    self.postMessage({ id, error: "Invalid study worker request" });
    return;
  }
  clearDataDiagnostics();
  self.postMessage({ id: data.id, state: "running" });
  const computeStartedAt = performance.now();
  try {
    const result = computeStudyTask(data.task);
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
