import { describe, expect, it, vi } from "vitest";
import { PendingMaiaCommandError, requestMaiaApi } from "../../scripts/maia-coverage-client.mjs";

const response = (status: number, body: unknown) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

describe("Maia background command receipts", () => {
  it("Docker engine callback waits for its PostgreSQL receipt with the worker header", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response(202, { operation_id: "position-save", state: "pending" }))
      .mockResolvedValueOnce(response(200, { state: "complete", response: { status: "complete" } }));
    const result = await requestMaiaApi("http://tempo", "/api/games/analysis/position/report/report",
      { method: "POST", headers: { "X-Tempo-Engine-Worker": "docker" } },
      { fetchImpl, pause: async () => {} });
    expect(result).toEqual({ status: "complete" });
    expect(fetchImpl.mock.calls[0][1].headers["X-Tempo-Engine-Worker"]).toBe("docker");
    expect(fetchImpl.mock.calls[0][1].headers["Idempotency-Key"]).toBeTruthy();
    expect(fetchImpl.mock.calls[1][0]).toBe("http://tempo/api/operations/position-save");
  });

  it("keeps one idempotency key after broker retry and waits for a completed receipt", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response(503, { detail: "broker unavailable" }))
      .mockResolvedValueOnce(response(202, { operation_id: "maia-one", state: "pending" }))
      .mockResolvedValueOnce(response(200, { state: "pending" }))
      .mockResolvedValueOnce(response(200, { state: "complete", response: { job: { node_id: "node-one" } } }));
    const result = await requestMaiaApi("http://tempo", "/api/repertoire-coverage/maia/claim",
      { method: "POST" }, { fetchImpl, pause: async () => {} });
    expect(result).toEqual({ job: { node_id: "node-one" } });
    expect(fetchImpl.mock.calls[0][1].headers["Idempotency-Key"]).toBeTruthy();
    expect(fetchImpl.mock.calls[0][1].headers["Idempotency-Key"])
      .toBe(fetchImpl.mock.calls[1][1].headers["Idempotency-Key"]);
    expect(fetchImpl.mock.calls[2][0]).toBe("http://tempo/api/operations/maia-one");
    expect(fetchImpl.mock.calls[2][1].headers["X-Tempo-Work-Class"]).toBe("background");
  });

  it("reports a failed command receipt instead of accepting its pending response", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(response(202, { operation_id: "maia-failed", state: "pending" }))
      .mockResolvedValueOnce(response(200, { state: "failed", error: { message: "lease expired" } }));
    await expect(requestMaiaApi("http://tempo", "/api/repertoire-coverage/maia/submit",
      { method: "POST" }, { fetchImpl, pause: async () => {} }))
      .rejects.toThrow("lease expired");
  });

  it("keeps an unresolved command pending instead of reporting a Maia failure", async () => {
    const fetchImpl = vi.fn().mockImplementation((url: string) => Promise.resolve(
      url.endsWith("/submit")
        ? response(202, { operation_id: "maia-pending", state: "pending" })
        : response(200, { state: "pending" }),
    ));
    await expect(requestMaiaApi("http://tempo", "/api/repertoire-coverage/maia/submit",
      { method: "POST" }, { fetchImpl, pause: async () => {} }))
      .rejects.toMatchObject({ operationId: "maia-pending", name: PendingMaiaCommandError.name });
  });
});
