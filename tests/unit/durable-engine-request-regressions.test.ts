import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { createDurableEngineRequest, migrateLegacyDefenseClaimJournal } from "../../scripts/durable-engine-request.mjs";
import { requestMaiaApi } from "../../scripts/maia-coverage-client.mjs";
import { confirmOperationResponse, PendingOperationError } from "../../app/lib/operation-status";

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

  it("moves a legacy unresolved defensive claim to its dedicated durable journal", async () => {
    const directory = mkdtempSync(join(tmpdir(), "tempo-engine-journal-"));
    const journalPath = join(directory, "pending.json");
    try {
      const legacy = createDurableEngineRequest("http://api", journalPath,
        async () => { throw new Error("uncertain claim"); });
      await expect(legacy.send("/api/defensive-threats/analysis/claim", { method: "POST" }))
        .rejects.toThrow("uncertain claim");
      const original = JSON.parse(readFileSync(journalPath, "utf8"));
      await migrateLegacyDefenseClaimJournal(journalPath);
      expect(JSON.parse(readFileSync(`${journalPath}.defense`, "utf8")).operationId)
        .toBe(original.operationId);
      const ordinary = createDurableEngineRequest("http://api", journalPath,
        async () => ({ job: { id: "game" } }));
      expect(await ordinary.send("/api/games/analysis/position/claim", { method: "POST" }))
        .toEqual({ job: { id: "game" } });
    } finally { rmSync(directory, { recursive: true, force: true }); }
  });

  it("returns blocked operation details without discarding an uncertain engine command", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(Response.json({ operation_id: "claim-one", state: "executing" }, { status: 202 }))
      .mockResolvedValueOnce(Response.json({ state: "blocked", last_error: { message: "timeout budget exhausted" } }));
    await expect(requestMaiaApi("http://api", "/api/defensive-threats/analysis/claim",
      { method: "POST", operationId: "claim-one", pollAttempts: 1 },
      { fetchImpl, pause: async () => undefined })).rejects.toMatchObject({
      operationId: "claim-one",
      message: expect.stringContaining("timeout budget exhausted"),
    });
  });

  it("reports a blocked browser operation without losing its original identity", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({
      state: "blocked", last_error: { message: "transaction budget exhausted" },
    })));
    await expect(confirmOperationResponse(Response.json(
      { operation_id: "original-id", state: "queued" }, { status: 202 },
    ))).rejects.toMatchObject({
      operationId: "original-id", blocked: true,
      message: expect.stringContaining("transaction budget exhausted"),
    } satisfies Partial<PendingOperationError>);
    vi.unstubAllGlobals();
  });
});
