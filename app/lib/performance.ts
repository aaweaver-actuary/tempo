export type TempoTiming = {
  operation: "board-ready" | "move-to-paint" | "view-switch";
  duration: number;
  recordedAt: number;
};
const timings: TempoTiming[] = [];

export function measureTempoOperation(operation: TempoTiming["operation"]) {
  const startedAt = performance.now();
  return () => {
    const endedAt = performance.now();
    timings.push({
      operation,
      duration: endedAt - startedAt,
      recordedAt: Date.now(),
    });
    if (typeof performance.measure === "function") {
      performance.clearMeasures?.(`tempo:${operation}`);
      performance.measure(`tempo:${operation}`, {
        start: startedAt,
        end: endedAt,
      });
    }
    if (timings.length > 200) timings.shift();
  };
}

export function tempoPerformanceTimings(): readonly TempoTiming[] {
  return timings.slice();
}
