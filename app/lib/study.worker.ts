import { computeStudyTask, type StudyTask } from "./study-computation";

self.onmessage = ({ data }: MessageEvent<{ id: number; task: StudyTask }>) => {
  try { self.postMessage({ id: data.id, result: computeStudyTask(data.task) }); }
  catch (error) { self.postMessage({ id: data.id, error: error instanceof Error ? error.message : String(error) }); }
};
