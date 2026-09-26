import { afterEach, expect, it, vi } from "vitest";
import { runStudyTask } from "../../app/lib/background-study";
import { clearDebugErrors, debugErrors } from "../../app/lib/debug-reporting";

afterEach(() => {
  vi.unstubAllGlobals();
  clearDebugErrors();
});

it("iPhone study worker startup failure is actionable and records the safe script path", async () => {
  class FailingWorker {
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    postMessage() {
      this.onerror?.({
        message: "Script error.", filename: "https://tempo.example/assets/study.worker.js?secret=token",
        lineno: 3, colno: 2,
      } as ErrorEvent);
    }
    terminate() {}
  }
  vi.stubGlobal("Worker", FailingWorker);
  await expect(runStudyTask({ kind: "queue", payload: { cards: [], count: 0 } }))
    .rejects.toThrow("Reopen Tempo while connected");
  expect(debugErrors().at(-1)?.context).toMatchObject({
    source: "study-worker", scriptPath: "/assets/study.worker.js", line: 3, column: 2,
  });
});
