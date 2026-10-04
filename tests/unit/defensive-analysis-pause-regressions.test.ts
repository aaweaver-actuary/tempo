// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createEngineSearch, admitEngineJob, engineSearchAllowed, engineSearchWasPreempted } from "../../scripts/engine-search.mjs";
import { recoverNextEngineJob } from "../../scripts/engine-job-recovery.mjs";
import { createDurableEngineRequest } from "../../scripts/durable-engine-request.mjs";
import { runEngineWorkerCycle } from "../../scripts/engine-worker-cycle.mjs";

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

it("a pending defensive report survives pause and restart and delivers before another claim", async () => {
  const directory = mkdtempSync(join(tmpdir(), "tempo-paused-report-"));
  try {
    const journalPath = join(directory, "pending.json");
    const pendingJournal = createDurableEngineRequest("http://api", journalPath,
      async (_api: string, _path: string, options: { operationId?: string } = {}) => {
        throw Object.assign(new Error("receipt pending"), { operationId: options.operationId });
      });
    const reportPath = `/api/defensive-threats/analysis/${job.id}/report`;
    const body = JSON.stringify({ lease_id: job.lease_id, report: { complete: true } });
    await expect(pendingJournal.send(reportPath, { method: "POST", body })).rejects.toThrow("receipt pending");
    const saved = await pendingJournal.pending();
    const delivered = vi.fn().mockResolvedValue({ status: "complete" });
    const restarted = createDurableEngineRequest("http://api", journalPath, delivered);
    const defenseJournal = { recover: vi.fn().mockResolvedValue(null) };
    const request = vi.fn().mockResolvedValue({ active: true, search_allowed: false });
    const searches = { fatalEngineError: false, evaluate: vi.fn() };
    const cycle = { searches, request, durableRequest: restarted, defenseClaimRequest: defenseJournal,
      sleep: vi.fn().mockResolvedValue(undefined), shutdown: vi.fn(), logError: vi.fn() };
    await runEngineWorkerCycle(cycle);
    expect(delivered.mock.calls[0]).toMatchObject(["http://api", reportPath, {
      operationId: saved.operationId, body,
    }]);
    expect(delivered.mock.invocationCallOrder[0]).toBeLessThan(defenseJournal.recover.mock.invocationCallOrder[0]);
    expect(await restarted.pending()).toBeNull();
    await runEngineWorkerCycle(cycle);
    expect(delivered).toHaveBeenCalledTimes(1);
    expect(searches.evaluate).not.toHaveBeenCalled();
    expect(request.mock.calls.every(call => call[0] === '/api/system/foreground-active')).toBe(true);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
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

it("a paused engine that cannot drain releases its lease without recording failure and cleans every timer", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  engine.drain = false;
  const searches = createEngineSearch(engine, vi.fn().mockResolvedValue({ foreground_active: false, search_allowed: false }));
  const result = searches.evaluate(job).catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(5_750);
  const cancellation = await result;
  expect(cancellation).toMatchObject({ message: "Stockfish did not drain after cancellation" });
  expect(engineSearchWasPreempted(cancellation)).toBe(true);
  expect(engineSearchWasPreempted(new Error("Engine timed out before requested depth"))).toBe(false);
  expect(searches.fatalEngineError).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

it.each([true, false])("pause near depth deadline preserves preemption through drain (drains=%s)", async (drains) => {
  vi.useFakeTimers();
  const engine = new Engine();
  engine.drain = false;
  let paused = false;
  const control = vi.fn(async () => ({ foreground_active: false, search_allowed: !paused }));
  const searches = createEngineSearch(engine, control);
  const outcome = searches.evaluate(job).catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(53_250);
  paused = true;
  await vi.advanceTimersByTimeAsync(750);
  expect(engine.commands.filter(command => command === "stop")).toHaveLength(1);
  if (drains) {
    await vi.advanceTimersByTimeAsync(1_500);
    engine.finish();
  } else {
    await vi.advanceTimersByTimeAsync(5_000);
  }
  const cancellation = await outcome;
  expect(cancellation).toMatchObject({ diagnostics: { outcome: "preempted" } });
  expect(engineSearchWasPreempted(cancellation)).toBe(true);
  expect(cancellation).not.toHaveProperty("report");
  expect(engine.commands.filter(command => command === "stop")).toHaveLength(1);
  expect(searches.fatalEngineError).toBe(!drains);
  expect(vi.getTimerCount()).toBe(0);
});

it("depth deadline first remains timeout when a later outstanding control reports pause", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  engine.drain = false;
  let resolveLateControl!: (value: object) => void;
  let deferControl = false;
  const control = vi.fn(() => deferControl
    ? new Promise(resolve => { resolveLateControl = resolve; })
    : Promise.resolve({ foreground_active: false, search_allowed: true }));
  const searches = createEngineSearch(engine, control);
  const result = searches.evaluate(job).catch((error: Error) => error);
  await vi.advanceTimersByTimeAsync(54_000);
  deferControl = true;
  await vi.advanceTimersByTimeAsync(1_000);
  resolveLateControl({ foreground_active: false, search_allowed: false });
  await vi.advanceTimersByTimeAsync(500);
  engine.finish();
  const timeout = await result;
  expect(timeout).toMatchObject({ message: "Engine timed out before requested depth", diagnostics: { outcome: "timeout" } });
  expect(engineSearchWasPreempted(timeout)).toBe(false);
  expect(engine.commands.filter(command => command === "stop")).toHaveLength(1);
  expect(searches.fatalEngineError).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});

it("old control response cannot stop the next search after the previous search completed", async () => {
  vi.useFakeTimers();
  const engine = new Engine();
  let resolveOldControl!: (value: object) => void;
  const control = vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOldControl = resolve; }))
    .mockResolvedValue({ foreground_active: false, search_allowed: true });
  const searches = createEngineSearch(engine, control);
  const first = searches.evaluate(job);
  await vi.advanceTimersByTimeAsync(750);
  engine.finish();
  await expect(first).resolves.toHaveProperty('report.complete', true);
  const second = searches.evaluate({ ...job, id: 'next-request' });
  resolveOldControl({ foreground_active: false, search_allowed: false });
  await vi.advanceTimersByTimeAsync(750);
  expect(engine.commands).not.toContain('stop');
  engine.finish();
  await expect(second).resolves.toHaveProperty('report.complete', true);
  expect(vi.getTimerCount()).toBe(0);
});

type CallbackOptions = { operationId?: string; body?: string; signal?: AbortSignal; pollAttempts?: number };

async function createProductionCycleHarness(callbackTransport: (
  api: string, path: string, options?: CallbackOptions,
) => Promise<{ status: string }> = async () => ({ status: "queued" })) {
  const directory = mkdtempSync(join(tmpdir(), 'tempo-cancellation-cycle-'));
  const journalPath = join(directory, 'pending.json');
  const originalClaim = createDurableEngineRequest('http://api', `${journalPath}.defense`,
    async (_api: string, _path: string, options: CallbackOptions = {}) => {
      throw Object.assign(new Error('pending original claim'), { operationId: options.operationId });
    });
  await expect(originalClaim.send('/api/defensive-threats/analysis/claim', { method: 'POST' }))
    .rejects.toThrow('pending original claim');
  const durableRequest = createDurableEngineRequest('http://api', journalPath, callbackTransport);
  const defenseClaimRequest = createDurableEngineRequest('http://api', `${journalPath}.defense`, async () => ({ job }));
  const recoverClaim = vi.spyOn(defenseClaimRequest, 'recover');
  const controls = { paused: false, foregroundActive: false };
  const request = vi.fn(async (path: string, options: { method?: string; body?: string } = {}, deliveryOptions = {}) => {
    if (options.method === 'POST') return durableRequest.send(path, options, deliveryOptions);
    return { active: controls.foregroundActive, foreground_active: false, search_allowed: !controls.paused };
  });
  const engine = new Engine();
  const searches = createEngineSearch(engine, request);
  let notifyStarted!: () => void;
  const started = new Promise<void>(resolve => { notifyStarted = resolve; });
  const originalEvaluate = searches.evaluate;
  const evaluate = vi.spyOn(searches, 'evaluate').mockImplementation((...arguments_) => {
    const result = originalEvaluate(...arguments_);
    notifyStarted();
    return result;
  });
  const dependencies = { searches, request, durableRequest, defenseClaimRequest,
    sleep: vi.fn().mockResolvedValue(undefined), shutdown: vi.fn(), logError: vi.fn() };
  return { directory, controls, engine, started, evaluate, recoverClaim, dependencies };
}

it.each(['pause drains', 'pause fatal', 'deadline first', 'engine fault'])(
  'production worker routes cancellation and genuine failure outcomes (%s)', async scenario => {
    vi.useFakeTimers();
    const callbacks = vi.fn().mockResolvedValue({ status: 'queued' });
    const harness = await createProductionCycleHarness(callbacks);
    try {
      harness.engine.drain = false;
      const cycle = runEngineWorkerCycle(harness.dependencies);
      await harness.started;
      if (scenario === 'engine fault') {
        harness.engine.onError('Stockfish crashed');
      } else if (scenario.startsWith('pause')) {
        await vi.advanceTimersByTimeAsync(53_250);
        harness.controls.paused = true;
        await vi.advanceTimersByTimeAsync(750);
      } else {
        await vi.advanceTimersByTimeAsync(55_000);
      }
      if (scenario === 'pause fatal') {
        await vi.advanceTimersByTimeAsync(5_000);
      } else if (scenario !== 'engine fault') {
        await vi.advanceTimersByTimeAsync(1_500);
        harness.engine.finish();
      }
      await cycle;
      expect(callbacks).toHaveBeenCalledTimes(1);
      expect(callbacks.mock.calls[0][1]).toBe(`/api/defensive-threats/analysis/${job.id}/${scenario.startsWith('pause') ? 'release' : 'failure'}`);
      expect(JSON.parse(callbacks.mock.calls[0][2].body)).toMatchObject({ lease_id: job.lease_id,
        diagnostics: { outcome: scenario === 'engine fault' ? 'failure' : scenario === 'deadline first' ? 'timeout' : 'preempted' } });
      expect(harness.engine.commands.filter(command => command === 'stop')).toHaveLength(scenario === 'engine fault' ? 0 : 1);
      const fatal = scenario === 'pause fatal' || scenario === 'engine fault';
      expect(harness.dependencies.shutdown).toHaveBeenCalledTimes(fatal ? 1 : 0);
      if (fatal) {
        await runEngineWorkerCycle(harness.dependencies);
        expect(harness.evaluate).toHaveBeenCalledTimes(1);
        expect(callbacks).toHaveBeenCalledTimes(1);
        expect(() => harness.dependencies.searches.evaluate(job)).toThrow('Stockfish is unavailable');
      }
      expect(vi.getTimerCount()).toBe(0);
    } finally { rmSync(harness.directory, { recursive: true, force: true }); }
  },
);

it('fatal cancellation bounds delivery and recovers the unchanged release before claims after restart', async () => {
  vi.useFakeTimers();
  let hangDelivery = true;
  let recoveryPending = true;
  const callbacks = vi.fn(async (_api: string, _path: string, options: CallbackOptions = {}) => {
    if (hangDelivery) return new Promise<{ status: string }>((_resolve, reject) => {
      options.signal!.addEventListener('abort', () => reject(new Error('delivery aborted')), { once: true });
    });
    if (recoveryPending) throw Object.assign(new Error('receipt pending'), { operationId: options.operationId });
    return { status: 'queued' };
  });
  const harness = await createProductionCycleHarness(callbacks);
  try {
    harness.engine.drain = false;
    const firstCycle = runEngineWorkerCycle(harness.dependencies);
    await harness.started;
    harness.controls.paused = true;
    await vi.advanceTimersByTimeAsync(5_750);
    // Wait for the real durable envelope write to finish before advancing its delivery deadline.
    await vi.waitFor(() => expect(callbacks).toHaveBeenCalledTimes(1));
    const saved = await harness.dependencies.durableRequest.pending();
    expect(saved.path).toBe(`/api/defensive-threats/analysis/${job.id}/release`);
    expect(saved.options).not.toHaveProperty('signal');
    expect(saved.options).not.toHaveProperty('pollAttempts');
    await vi.advanceTimersByTimeAsync(1_500);
    await firstCycle;
    expect(harness.dependencies.shutdown).toHaveBeenCalledTimes(1);
    expect(await harness.dependencies.durableRequest.pending()).toEqual(saved);
    await runEngineWorkerCycle(harness.dependencies);
    expect(callbacks).toHaveBeenCalledTimes(1);
    expect(harness.evaluate).toHaveBeenCalledTimes(1);
    hangDelivery = false;
    const restarted = { ...harness.dependencies, searches: createEngineSearch(new Engine(), harness.dependencies.request) };
    await runEngineWorkerCycle(restarted);
    expect(await restarted.durableRequest.pending()).toEqual(saved);
    expect(harness.recoverClaim).toHaveBeenCalledTimes(1);
    recoveryPending = false;
    harness.controls.foregroundActive = true;
    await runEngineWorkerCycle(restarted);
    expect(await restarted.durableRequest.pending()).toBeNull();
    expect(callbacks.mock.calls.map(call => [call[1], call[2]?.operationId, call[2]?.body]))
      .toEqual(Array(3).fill([saved.path, saved.operationId, saved.options.body]));
    expect(harness.dependencies.request.mock.calls.filter(call => call[1]?.method === 'POST')).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  } finally { rmSync(harness.directory, { recursive: true, force: true }); }
});
