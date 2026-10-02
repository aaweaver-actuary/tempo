// @vitest-environment node
import { execFileSync } from "node:child_process";
import { expect, it } from "vitest";
import { resolvePython } from "../../scripts/resolve-python.mjs";
import { backgroundDiagnosticsSchema, engineAttemptDiagnosticsSchema } from "../../app/domain/schemas/background-diagnostics";
import { backgroundDiagnosticsSnapshot, setBackgroundDiagnostics } from "../../app/lib/service-status";
import { startEngineAttempt } from "../../scripts/engine-attempt-diagnostics.mjs";

function fixture() {
  return JSON.parse(execFileSync(resolvePython(), ["-c", `
from app.services.background_metrics import BackgroundDiagnostics,KindCounts,MetricCounts,QueueDiagnostic
from app.services.background_runtime import RuntimeSnapshot
print(BackgroundDiagnostics(generated_at='2026-10-02T12:00:00+00:00',window_start='2026-10-01T12:05:00+00:00',window_end='2026-10-02T12:00:00+00:00',query_duration_seconds=0.002,available=True,runtime=RuntimeSnapshot(),queues=[QueueDiagnostic(queue='durable',state='retrying',count=1,oldest_pending_age_seconds=600)],counters=[KindCounts(kind='engine_game',counts=MetricCounts(engine_preemptions=2,engine_preempted_seconds=3.5),useful_completion_unit='accepted_position')]).model_dump_json())
`], { env: { ...process.env, PYTHONPATH: "backend" }, encoding: "utf8" }));
}

it("background diagnostics Python and TypeScript schemas preserve missing counters and reject unsafe public fields", () => {
  const raw = fixture();
  expect(backgroundDiagnosticsSchema.parse(raw)).toEqual(raw);
  const missing = structuredClone(raw);
  missing.counters[0].counts = {};
  expect(backgroundDiagnosticsSchema.parse(missing).counters[0].counts.engine_preemptions).toBe(0);
  for (const value of [ { ...raw, payload: { token: "canary-secret" } }, { ...raw, schema_version: 2 },
    { ...raw, counters: [{ kind: "private-operation-id", counts: {} }] },
    { ...raw, query_duration_seconds: -1 } ]) {
    expect(backgroundDiagnosticsSchema.safeParse(value).success).toBe(false);
  }
  expect(engineAttemptDiagnosticsSchema.safeParse({ outcome: "success", elapsed_seconds: Infinity }).success).toBe(false);
});

it("background diagnostics cache accepts only the bounded aggregate contract", () => {
  const raw = fixture();
  setBackgroundDiagnostics(raw);
  expect(backgroundDiagnosticsSnapshot()).toEqual(raw);
  expect(() => setBackgroundDiagnostics({ ...raw, lease_token: "canary-secret" })).toThrow();
  expect(JSON.stringify(backgroundDiagnosticsSnapshot())).not.toContain("canary-secret");
  setBackgroundDiagnostics(null);
  expect(backgroundDiagnosticsSnapshot()).toBeNull();
});

it("engine preemption monotonic seconds are separate from successful work and each search emits once", () => {
  let milliseconds = 100;
  const logs: string[] = [];
  const finishPreempted = startEngineAttempt("engine_game", () => milliseconds, (message: string) => logs.push(message));
  milliseconds = 3600;
  expect(finishPreempted("preempted")).toEqual({ outcome: "preempted", elapsed_seconds: 3.5 });
  milliseconds = 5000;
  expect(finishPreempted("failure")).toEqual({ outcome: "preempted", elapsed_seconds: 3.5 });
  expect(logs).toHaveLength(1);
  const success = startEngineAttempt("engine_game", () => milliseconds, (message: string) => logs.push(message));
  milliseconds = 7000;
  expect(success("success")).toEqual({ outcome: "success", elapsed_seconds: 2 });
  expect(logs).toHaveLength(2);
  expect(logs.join()).not.toMatch(/request|position|lease|token/);
});
