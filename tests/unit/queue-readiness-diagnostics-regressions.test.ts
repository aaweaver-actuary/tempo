// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import type { APIRequestContext, TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { verifyQueueReadinessWithDiagnostics } from "../browser/queue-readiness-diagnostics";

it("queue failure diagnostics retain the original assertion and redact runner secrets before workers are replaced", async () => {
  const failure = new Error("Initial disposable queue is not ready");
  const attach = vi.fn().mockResolvedValue(undefined);
  const readDockerOutput = vi.fn().mockReturnValue("password sample-secret; Bearer private-token");
  await expect(verifyQueueReadinessWithDiagnostics(() => Promise.reject(failure), attach, {
    composeProject: "tempo-pg-regressions-123-a1b2", secretValues: ["sample-secret"], readDockerOutput,
  })).rejects.toBe(failure);
  expect(readDockerOutput).toHaveBeenCalledTimes(4);
  expect(readDockerOutput.mock.calls.every(([args]) => args.slice(0, 3).join(" ") === "compose -p tempo-pg-regressions-123-a1b2")).toBe(true);
  expect(attach).toHaveBeenCalledTimes(4);
  expect(readDockerOutput.mock.calls[2][0]).toContain("ps");
  expect(readDockerOutput.mock.calls[3][0]).toContain("background-scheduler");
  expect(readDockerOutput.mock.calls[3][0].at(-1)).toContain("/proc/1/wchan");
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

vi.mock("node:child_process", () => {
  const execFileSync = vi.fn();
  return { execFileSync, default: { execFileSync } };
});

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllEnvs();
});

async function captureReadinessFailure(request: APIRequestContext, testInfo: TestInfo) {
  const failure = new Error("Original queue readiness failure");
  await expect(verifyQueueReadinessWithDiagnostics(() => Promise.reject(failure),
    testInfo.attach.bind(testInfo), { request, api: "http://fixture/api", secretValues: [] },
  )).rejects.toBe(failure);
}

function diagnosticsRequest(disposable: boolean) {
  return { get: vi.fn(async (url: string) => ({
    status: () => 200, ok: () => true,
    json: async () => url.endsWith("/health") ? { test_instance: disposable }
      : { available: true },
  })) };
}

it("issue135 queue failure evidence reads one owned disposable task with bounded diagnostics", async () => {
  vi.stubEnv("TEMPO_TEST_COMPOSE_PROJECT", "tempo-pg-regressions-135-abcdef01");
  vi.mocked(execFileSync).mockReturnValue("{\"current_daily_queue\":{\"phase\":\"unlock_opening\"}}");
  const request = diagnosticsRequest(true);
  const testInfo = { attach: vi.fn() };
  await captureReadinessFailure(request as unknown as APIRequestContext, testInfo as unknown as TestInfo);
  expect(request.get).toHaveBeenCalledWith("http://fixture/api/system/background-diagnostics", {
    timeout: 2_000, headers: { "X-Tempo-Work-Class": "background" },
  });
  const [, arguments_, options] = vi.mocked(execFileSync).mock.calls[0];
  expect(arguments_).toContain("tempo-pg-regressions-135-abcdef01");
  const query = arguments_?.at(-1) as string;
  expect(query).toContain("BEGIN READ ONLY");
  expect(query).toContain("statement_timeout='100ms'");
  expect(query).toContain("deduplication_key='current'");
  expect(query).toContain("transaction_timeout_count");
  expect(query).toContain("LIMIT 20");
  expect(query).not.toContain("SELECT *");
  expect(options).toMatchObject({ timeout: 5_000, maxBuffer: 128 * 1024 });
  expect(testInfo.attach).toHaveBeenCalledWith("queue-state-at-readiness-failure", expect.objectContaining({ contentType: "text/plain" }));
});

it.each([false, true])("issue135 unavailable queue evidence retains the original failure without accessing live resources disposable=%s", async (disposable) => {
  vi.stubEnv("TEMPO_TEST_COMPOSE_PROJECT", disposable ? "tempo-live" : "tempo-pg-regressions-135-abcdef01");
  const request = diagnosticsRequest(disposable);
  const testInfo = { attach: vi.fn() };
  await captureReadinessFailure(request as unknown as APIRequestContext, testInfo as unknown as TestInfo);
  expect(execFileSync).not.toHaveBeenCalled();
  expect(testInfo.attach).toHaveBeenCalledWith("queue-readiness-task-evidence-unavailable", expect.objectContaining({ contentType: "text/plain" }));
});

it("issue135 queue evidence reports unavailable HTTP and Docker diagnostics without hiding the readiness failure", async () => {
  vi.stubEnv("TEMPO_TEST_COMPOSE_PROJECT", "tempo-pg-regressions-135-abcdef01");
  const request = diagnosticsRequest(true);
  request.get.mockImplementation(async (url: string) => {
    if (!url.endsWith("/health")) throw new Error("diagnostics read unavailable");
    return { status: () => 200, ok: () => true, json: async () => ({ test_instance: true }) };
  });
  vi.mocked(execFileSync).mockImplementation(() => { throw new Error("bounded SQL deadline"); });
  const testInfo = { attach: vi.fn() };
  await captureReadinessFailure(request as unknown as APIRequestContext, testInfo as unknown as TestInfo);
  expect(testInfo.attach.mock.calls.find(([name]) => name === "queue-readiness-diagnostics")?.[1].body.toString()).toContain("diagnostics read unavailable");
  expect(testInfo.attach.mock.calls.find(([name]) => name === "queue-state-at-readiness-failure")?.[1].body.toString()).toContain("bounded SQL deadline");
  expect(testInfo.attach).toHaveBeenCalledTimes(5);
});
