// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { createEngineSearch, admitEngineJob, engineSearchAllowed } from "../../scripts/engine-search.mjs";
import { recoverNextEngineJob } from "../../scripts/engine-job-recovery.mjs";

const job = { id: "defensive-request", lease_id: "current-lease", request: {
  position_start_fen: "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  position_prefix_uci: [], depth: 14, multipv: 1,
} };
class Engine {
  listen = (text: string) => { void text; };
  onError = (text: string) => { void text; };
  drain = true;
  commands: string[] = [];
  uci(command: string) {
    this.commands.push(command);
    if (command === "stop" && this.drain) this.listen("bestmove e2e4");
  }
  finish() {
    this.listen("info depth 14 multipv 1 score cp 20 pv e2e4 e7e5");
    this.listen("bestmove e2e4");
  }
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

it("pausing an active defensive search drains once and records preemption without a partial report", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  const control = vi.fn().mockResolvedValue({ foreground_active: false, search_allowed: false });
  const searches = createEngineSearch(engine, control);
  const outcome = searches.evaluate(job).catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(750);
  expect(await outcome).toMatchObject({ message: "preempted", diagnostics: { outcome: "preempted" } });
  expect(engine.commands.filter(command => command === "stop")).toHaveLength(1);
  expect(searches.fatalEngineError).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});

it("paused defensive claims recovered after restart are durably released before search", async () => {
  const defenseJournal = { recover: vi.fn().mockResolvedValue({ result: { job } }) };
  const recovered = await recoverNextEngineJob({ recover: vi.fn().mockResolvedValue(null) }, defenseJournal);
  const request = vi.fn().mockResolvedValueOnce({ foreground_active: false, search_allowed: false })
    .mockResolvedValueOnce({ status: "queued" });
  expect(await admitEngineJob(recovered.job, "engine_defense", request)).toBe(false);
  expect(request.mock.calls[1]).toEqual([`/api/defensive-threats/analysis/${job.id}/release`, {
    method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
  }]);
});

it("an unresolved paused-job release propagates its durable operation instead of acquiring another job", async () => {
  const pending = Object.assign(new Error("pending release"), { operationId: "release-receipt" });
  const request = vi.fn().mockResolvedValueOnce({ foreground_active: false, search_allowed: false })
    .mockRejectedValueOnce(pending);
  await expect(admitEngineJob(job, "engine_defense", request)).rejects.toBe(pending);
});

it("repertoire recommendation permission allows a shared search while defensive analysis is paused", async () => {
  const request = vi.fn().mockResolvedValue({ foreground_active: false, search_allowed: true });
  expect(await admitEngineJob(job, "engine_defense", request)).toBe(true);
  expect(request).toHaveBeenCalledTimes(1);
});

it("ordinary game searches retain foreground control and complete while defense is paused", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  const request = vi.fn().mockResolvedValue({ active: false });
  expect(await admitEngineJob(job, "engine_game", request)).toBe(true);
  expect(request).not.toHaveBeenCalled();
  const result = createEngineSearch(engine, request).evaluate(job, "engine_game");
  await vi.advanceTimersByTimeAsync(750);
  engine.finish();
  expect(await result).toMatchObject({ report: { complete: true }, diagnostics: { outcome: "success" } });
  expect(request.mock.calls[0][0]).toBe("/api/system/foreground-active");
  expect(vi.getTimerCount()).toBe(0);
});

it("engine control polling stays single-flight and a stale pause cannot stop a completed search", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  let resolveControl!: (value: object) => void;
  const request = vi.fn().mockImplementation(() => new Promise(resolve => { resolveControl = resolve; }));
  const result = createEngineSearch(engine, request).evaluate(job);
  await vi.advanceTimersByTimeAsync(2_250);
  expect(request).toHaveBeenCalledTimes(1);
  engine.finish();
  await expect(result).resolves.toMatchObject({ report: { complete: true } });
  resolveControl({ foreground_active: false, search_allowed: false });
  await vi.advanceTimersByTimeAsync(0);
  expect(engine.commands).not.toContain("stop");
  expect(vi.getTimerCount()).toBe(0);
});

it("a defensive control outage stops and releases rather than authorizing more computation", async () => {
  expect(await engineSearchAllowed(job, "engine_defense", vi.fn().mockRejectedValue(new Error("outage"))))
    .toBe(false);
});

it("a paused engine that cannot drain exits safely and cleans every search timer", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  engine.drain = false;
  const searches = createEngineSearch(engine, vi.fn().mockResolvedValue({ foreground_active: false, search_allowed: false }));
  const result = searches.evaluate(job).catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(5_750);
  expect(await result).toMatchObject({ message: "Stockfish did not drain after cancellation" });
  expect(searches.fatalEngineError).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});
