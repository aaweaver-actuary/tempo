/** Monotonic timing for one search. No chess payload enters diagnostics. */
export function startEngineAttempt(kind, now = () => performance.now(), log = console.info) {
  const started = now();
  let finished;
  return outcome => {
    if (!finished) {
      finished = { outcome, elapsed_seconds: Math.max(0, (now() - started) / 1000) };
      log(JSON.stringify({ event: "engine_search", kind, ...finished }));
    }
    return finished;
  };
}
let lastWaitingStage;
export function engineWaitingStage(kind, stage, log = console.info) {
  const waitingStage = `${kind}:${stage}`;
  if (lastWaitingStage === waitingStage) return;
  lastWaitingStage = waitingStage;
  log(JSON.stringify({ event: "engine_waiting", kind, stage, observed_at: new Date().toISOString() }));
}
