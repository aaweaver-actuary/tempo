import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { createDurableEngineRequest } from "../../scripts/durable-engine-request.mjs";
import { requestMaiaApi } from "../../scripts/maia-coverage-client.mjs";

describe("Docker engine command journal", () => {
  it("replays the same operation ID after a pending response and process restart", async () => {
    const directory = mkdtempSync(join(tmpdir(), "tempo-engine-journal-"));
    const journalPath = join(directory, "pending.json");
    try {
      const firstTransport = vi.fn(async (_api: string, _path: string,
        options: { operationId?: string } = {}) => {
        const error = new Error("receipt pending") as Error & { operationId?: string };
        error.operationId = options.operationId;
        throw error;
      });
      const first = createDurableEngineRequest("http://api", journalPath, firstTransport);
      await expect(first.send("/api/defensive-threats/analysis/claim", { method: "POST" }))
        .rejects.toThrow("receipt pending");
      const operationId = JSON.parse(readFileSync(journalPath, "utf8")).operationId;
      const secondTransport = vi.fn(async (_api: string, _path: string,
        _options: { operationId?: string } = {}) => ({ job: { id: "defense-one" } }));
      const restarted = createDurableEngineRequest("http://api", journalPath, secondTransport);
      expect(await restarted.recover()).toEqual({
        path: "/api/defensive-threats/analysis/claim", result: { job: { id: "defense-one" } },
      });
      expect(secondTransport.mock.calls[0][2]?.operationId).toBe(operationId);
      expect(await restarted.pending()).toBeNull();
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });

  it("keeps an uncertain callback but removes a definitive failed receipt", async () => {
    const directory = mkdtempSync(join(tmpdir(), "tempo-engine-journal-"));
    const journalPath = join(directory, "pending.json");
    try {
      const uncertain = createDurableEngineRequest("http://api", journalPath,
        async () => { throw new Error("connection reset"); });
      await expect(uncertain.send("/api/games/analysis/position/report-one/report",
        { method: "POST", body: JSON.stringify({ lease_id: "lease-one" }) }))
        .rejects.toThrow("connection reset");
      expect(await uncertain.pending()).not.toBeNull();
      const terminal = createDurableEngineRequest("http://api", journalPath,
        async () => { const error = new Error("stale lease") as Error & { terminal?: boolean };
          error.terminal = true; throw error; });
      await expect(terminal.recover()).rejects.toThrow("stale lease");
      expect(await terminal.pending()).toBeNull();
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });

  it("passes an explicit operation ID through the shared HTTP client", async () => {
    const fetchImpl = vi.fn(async (_url: string, _options: RequestInit) => new Response(JSON.stringify({ status: "complete" }),
      { status: 200, headers: { "Content-Type": "application/json" } }));
    await requestMaiaApi("http://api", "/api/games/analysis/repair-timeout",
      { method: "POST", operationId: "repair-stable" }, { fetchImpl });
    expect((fetchImpl.mock.calls[0][1].headers as Record<string, string>)["Idempotency-Key"])
      .toBe("repair-stable");
  });
});
