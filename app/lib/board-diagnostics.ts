// Fixed-size counters only: no subscriptions, storage, or per-pointer work.
const boardCounters = { acquisitions: 0, releases: 0, publications: 0,
  equivalentPublications: 0, rejectedPublications: 0, positionResets: 0, inputCancellations: 0 };
export function recordBoardEvent(event: keyof typeof boardCounters) {
  boardCounters[event] += 1;
}
export function boardDiagnostics() { return { ...boardCounters }; }
