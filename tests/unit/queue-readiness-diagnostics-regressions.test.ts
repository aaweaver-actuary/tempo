import { afterEach, expect, it, vi } from "vitest";
import type { APIRequestContext, TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { attachQueueReadinessDiagnostics } from "../browser/queue-readiness-diagnostics";

vi.mock("node:child_process", () => {
  const execFileSync = vi.fn();
  return { execFileSync, default: { execFileSync } };
});

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllEnvs();
});

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
  await attachQueueReadinessDiagnostics(request as unknown as APIRequestContext, "http://fixture/api", testInfo as unknown as TestInfo);
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
  expect(testInfo.attach).toHaveBeenCalledWith("queue-readiness-durable-task", expect.objectContaining({ contentType: "text/plain" }));
});

it.each([false, true])("issue135 unavailable queue evidence retains the original failure without accessing live resources disposable=%s", async (disposable) => {
  vi.stubEnv("TEMPO_TEST_COMPOSE_PROJECT", disposable ? "tempo-live" : "tempo-pg-regressions-135-abcdef01");
  const request = diagnosticsRequest(disposable);
  const testInfo = { attach: vi.fn() };
  await expect(attachQueueReadinessDiagnostics(request as unknown as APIRequestContext, "http://fixture/api", testInfo as unknown as TestInfo)).resolves.toBeUndefined();
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
  await expect(attachQueueReadinessDiagnostics(request as unknown as APIRequestContext, "http://fixture/api", testInfo as unknown as TestInfo)).resolves.toBeUndefined();
  expect(testInfo.attach).toHaveBeenCalledWith("queue-readiness-diagnostics", expect.objectContaining({ body: expect.stringContaining("diagnostics read unavailable") }));
  expect(testInfo.attach).toHaveBeenCalledWith("queue-readiness-task-evidence-unavailable", expect.objectContaining({ body: expect.stringContaining("bounded SQL deadline") }));
});
