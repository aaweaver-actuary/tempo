import { computeStudyTask } from "./study-computation";
import { studyRequestSchema } from "../domain/schemas";
import {
  clearDataDiagnostics,
  dataDiagnostics,
  parseData,
} from "./validated-data";

self.onmessage = ({ data: raw }: MessageEvent<unknown>) => {
  const data = parseData(studyRequestSchema, raw, "study worker request");
  clearDataDiagnostics();
  try {
    self.postMessage({
      id: data.id,
      result: computeStudyTask(data.task),
      diagnostics: dataDiagnostics(),
    });
  } catch (error) {
    self.postMessage({
      id: data.id,
      error: error instanceof Error ? error.message : String(error),
      diagnostics: dataDiagnostics(),
    });
  }
};
