import { afterEach, expect, it, vi } from "vitest";
import { readOperationResponse, retryBlockedOperation } from "../../app/lib/operation-status";
import { subscribeOperationStatusChange } from "../../app/lib/operation-status-events";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("frozen command recovery may replay an unknown receipt without treating pending receipts as missing", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state: "unknown" })));
  expect((await readOperationResponse("frozen-command", { allowMissing: true })).status).toBe(404);
  await expect(readOperationResponse("frozen-command")).rejects.toMatchObject({ name: "PendingOperationError" });
  vi.mocked(fetch).mockResolvedValueOnce(Response.json({ state: "pending" }));
  await expect(readOperationResponse("frozen-command", { allowMissing: true })).rejects.toMatchObject({ name: "PendingOperationError" });
});

it.each(["blocked", "unknown", "invalid", "pending", "queued", "executing", "retrying", "complete", "failed"])(
  "operation status resume signal preserves receipt semantics (%s)", async state => {
    const listener = vi.fn(); const unsubscribe = subscribeOperationStatusChange(listener);
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ state, response: { persisted: true }, error: { message: "Failed handler" } })));
    try {
      const result = await readOperationResponse("exact-operation").catch(error => error);
      if (state === "complete") expect(await result.json()).toEqual({ persisted: true });
      else expect(result).toMatchObject({ name: state === "failed" ? "FailedOperationError" : "PendingOperationError", operationId: "exact-operation" });
      expect(listener.mock.calls).toEqual(["blocked", "unknown", "invalid"].includes(state) ? [] : [["exact-operation"]]);
      expect(new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).has("X-Tempo-Work-Class")).toBe(false);
    } finally { unsubscribe(); }
  });

it("explicit operation retry signals only a proven nonblocked receipt despite throwing observers", async () => {
  const listener = vi.fn(() => { throw new Error("Observer failed"); });
  const unsubscribe = subscribeOperationStatusChange(listener);
  const requests: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    return init?.method === "POST" ? Response.json({ state: "blocked" }, { status: 202 }) : Response.json({ state: "retrying" });
  }));
  try {
    await expect(retryBlockedOperation("frozen-key")).rejects.toMatchObject({ name: "PendingOperationError", blocked: false, operationId: "frozen-key" });
    expect(listener).toHaveBeenCalledOnce(); expect(requests).toEqual([expect.stringMatching(/\/frozen-key\/retry$/), expect.stringMatching(/\/frozen-key$/)]);
  } finally { unsubscribe(); }
});
