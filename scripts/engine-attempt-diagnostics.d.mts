export type EngineDiagnostics = { outcome: "success" | "preempted" | "timeout" | "failure"; elapsed_seconds: number };
export function startEngineAttempt(kind: string, now?: () => number, log?: (message: string) => void): (outcome: EngineDiagnostics["outcome"]) => EngineDiagnostics;
export function engineWaitingStage(kind: string, stage: string, log?: (message: string) => void): void;
