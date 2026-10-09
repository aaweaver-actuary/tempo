// @vitest-environment node
import { expect, it, vi } from "vitest";
import { verifyQueueReadinessWithDiagnostics } from "../browser/queue-readiness-diagnostics";

it("queue failure diagnostics retain the original assertion and redact runner secrets before workers are replaced", async () => {
  const failure = new Error("Initial disposable queue is not ready");
  const attach = vi.fn().mockResolvedValue(undefined);
  const readDockerOutput = vi.fn().mockReturnValue("password sample-secret; Bearer private-token");
  await expect(verifyQueueReadinessWithDiagnostics(() => Promise.reject(failure), attach, {
    composeProject: "tempo-pg-regressions-123-a1b2", secretValues: ["sample-secret"], readDockerOutput,
  })).rejects.toBe(failure);
  expect(readDockerOutput).toHaveBeenCalledTimes(2);
  expect(readDockerOutput.mock.calls.every(([args]) => args.slice(0, 3).join(" ") === "compose -p tempo-pg-regressions-123-a1b2")).toBe(true);
  expect(attach).toHaveBeenCalledTimes(2);
  for (const [, attachment] of attach.mock.calls) {
    expect(attachment.body.toString()).toContain("[redacted]");
    expect(attachment.body.toString()).not.toMatch(/sample-secret|private-token/);
  }
});

it.each(["tempo", "other-task", "tempo-pg-regressions-123-a1b2;bad"])(
  "queue failure diagnostics refuse non-disposable target %s without masking the failure", async composeProject => {
    const failure = new Error("Not ready");
    const readDockerOutput = vi.fn();
    await expect(verifyQueueReadinessWithDiagnostics(() => Promise.reject(failure), vi.fn(), {
      composeProject, secretValues: [], readDockerOutput,
    })).rejects.toBe(failure);
    expect(readDockerOutput).not.toHaveBeenCalled();
  },
);

it("queue diagnostics capture remaining evidence after one read fails and never run on success", async () => {
  const failure = new Error("Not ready");
  const readDockerOutput = vi.fn().mockImplementationOnce(() => { throw new Error("Snapshot unavailable"); }).mockReturnValue("Worker waiting");
  const attach = vi.fn().mockResolvedValue(undefined);
  const options = { composeProject: "tempo-pg-regressions-123-a1b2", secretValues: [], readDockerOutput };
  await expect(verifyQueueReadinessWithDiagnostics(() => Promise.reject(failure), attach, options)).rejects.toBe(failure);
  expect(attach.mock.calls[0][1].body.toString()).toContain("diagnostic unavailable");
  expect(attach.mock.calls[1][1].body.toString()).toBe("Worker waiting");
  readDockerOutput.mockClear();
  await verifyQueueReadinessWithDiagnostics(() => Promise.resolve(), attach, options);
  expect(readDockerOutput).not.toHaveBeenCalled();
});
