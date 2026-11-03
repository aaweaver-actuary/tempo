// @vitest-environment node
import { expect, it } from "vitest";
import { startEngineAttempt } from "../../scripts/engine-attempt-diagnostics.mjs";

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
