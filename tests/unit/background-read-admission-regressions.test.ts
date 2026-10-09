import { afterEach, expect, it, vi } from "vitest";
import { backgroundReadWhenAdmitted } from "../../app/lib/background-fetch";
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
it("passive evidence reads resume after an explicit foreground wait without hiding a real failure", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValueOnce(Response.json({ detail: "Waiting for foreground activity" }, { status: 503, headers: { "Retry-After": "1" } }))
    .mockResolvedValueOnce(Response.json({ detail: "Database unavailable" }, { status: 503, headers: { "Retry-After": "1" } }));
  vi.stubGlobal("fetch", fetch);
  const pending = backgroundReadWhenAdmitted("/prefix-diagnostics", new AbortController().signal);
  await vi.advanceTimersByTimeAsync(1000);
  expect((await pending).status).toBe(503);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(new Headers(fetch.mock.calls[0][1].headers).get("X-Tempo-Work-Class")).toBe("background");
});
it("leaving passive evidence cancels its admission retry and frees every timer", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Waiting for foreground activity" }, { status: 503, headers: { "Retry-After": "1" } })));
  const controller = new AbortController();
  const pending = backgroundReadWhenAdmitted("/prefix-diagnostics", controller.signal);
  const rejected = expect(pending).rejects.toThrow("left evidence");
  await vi.advanceTimersByTimeAsync(0);
  controller.abort(new Error("left evidence"));
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
});
