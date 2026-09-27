export type TempoTiming = {
  operation: "board-ready" | "move-to-paint" | "view-switch" |
    "study-worker-queue" | "study-worker-compute" | "study-worker-roundtrip" |
    "study-match-coalesced-wait" | "api-response" | "workspace-data-ready";
  duration: number;
  recordedAt: number;
  resource?: string;
};
const timings: TempoTiming[] = [];

export function recordTempoDuration(operation: TempoTiming["operation"], duration: number, resource?: string) {
  timings.push({ operation, duration, recordedAt: Date.now(), ...(resource ? { resource } : {}) });
  if (timings.length > 200) timings.shift();
}

export function measureTempoOperation(operation: TempoTiming["operation"]) {
  const startedAt = performance.now();
  return () => {
    const endedAt = performance.now();
    recordTempoDuration(operation, endedAt - startedAt);
    if (typeof performance.measure === "function") {
      performance.clearMeasures?.(`tempo:${operation}`);
      performance.measure(`tempo:${operation}`, {
        start: startedAt,
        end: endedAt,
      });
    }
  };
}

export function tempoPerformanceTimings(): readonly TempoTiming[] {
  return timings.slice();
}
