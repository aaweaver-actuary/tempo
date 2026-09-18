import { expect, it, vi } from "vitest";
import { readWorkspaceData } from "../../app/lib/workspace-data";
it("workspace timeout is actionable and a retry can recover", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(options.signal.reason),
          );
        }),
    ),
  );
  const request = readWorkspaceData("/service.json").then(
    () => "unexpected success",
    (error) => error.message,
  );
  await vi.advanceTimersByTimeAsync(15_001);
  const outcome = await Promise.race([
    request,
    Promise.resolve("still loading"),
  ]);
  expect(outcome).toMatch(/timed out.*retry/i);
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ ready: true }));
  await expect(readWorkspaceData("/service.json")).resolves.toEqual({
    ready: true,
  });
});
