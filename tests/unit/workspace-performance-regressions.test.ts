// @vitest-environment node
import { expect, it, vi } from "vitest";
import { readWorkspaceData } from "../../app/lib/workspace-data";
import { tempoPerformanceTimings } from "../../app/lib/performance";

it("workspace fetch records each live API read without reusing a stale success", async () => {
  const resource = "/api/performance-fixture";
  const url = `http://localhost${resource}`;
  const fetcher = vi.fn(async () => Response.json({ count: 1 }));
  vi.stubGlobal("fetch", fetcher);
  const timingCountBefore = tempoPerformanceTimings().length;

  await expect(readWorkspaceData(url)).resolves.toEqual({ count: 1 });
  await expect(readWorkspaceData(url)).resolves.toEqual({ count: 1 });

  expect(fetcher).toHaveBeenCalledTimes(2);
  const timings = tempoPerformanceTimings().slice(timingCountBefore)
    .filter((timing) => timing.resource === resource);
  expect(timings.map((timing) => timing.operation)).toEqual([
    "api-response", "workspace-data-ready", "api-response", "workspace-data-ready",
  ]);
  expect(timings[0].duration).toBeGreaterThanOrEqual(0);
  expect(timings[1].duration).toBeGreaterThanOrEqual(timings[0].duration);
});

it("failed workspace fetch preserves HTTP status and records response without false ready timing", async () => {
  const resource = "/api/performance-failure";
  vi.stubGlobal("fetch", vi.fn(async () => new Response("Unavailable", { status: 503 })));
  const timingCountBefore = tempoPerformanceTimings().length;

  await expect(readWorkspaceData(`http://localhost${resource}`)).rejects.toThrow("HTTP 503");

  expect(tempoPerformanceTimings().slice(timingCountBefore)
    .filter((timing) => timing.resource === resource)
    .map((timing) => timing.operation)).toEqual(["api-response"]);
});
