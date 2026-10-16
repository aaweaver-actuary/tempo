// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it('study input terminates confirmed bulk computation immediately and preserves essential study capacity', async () => {
  vi.resetModules(); vi.useFakeTimers();
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: { data: unknown }) => void) | null = null;
    onerror = null; onmessageerror = null;
    terminate = vi.fn(); postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal('Worker', ControlledWorker);
  const { runStudyTask } = await import('../../app/lib/background-study');
  const priority = await import('../../app/lib/study-priority');
  const bulk = runStudyTask({ kind: 'repertoirePreparation', generation: 1, payload: { lines: [] } });
  const bulkId = workers[0].postMessage.mock.calls[0][0].id;
  workers[0].onmessage?.({ data: { id: bulkId, state: 'running' } });
  const staleReply = workers[0].onmessage;
  priority.markStudyInteraction();
  expect(workers[0].terminate).toHaveBeenCalledOnce();
  const essential = runStudyTask({ kind: 'queue', payload: { cards: [], count: 0 } });
  const essentialId = workers[1].postMessage.mock.calls[0][0].id;
  workers[1].onmessage?.({ data: { id: essentialId, result: [] } });
  await expect(essential).resolves.toEqual([]);
  staleReply?.({ data: { id: bulkId, result: 'obsolete' } });
  await vi.advanceTimersByTimeAsync(3_000);
  expect(workers).toHaveLength(3);
  expect(workers[1].terminate).not.toHaveBeenCalled();
  workers[2].onmessage?.({ data: { id: bulkId, result: 'current' } });
  await expect(bulk).resolves.toBe('current');
});

it('three started bulk invalidations stop actual workers, settle callers and reject late publications', async () => {
  vi.resetModules();
  const workers: ControlledWorker[] = [];
  class ControlledWorker {
    onmessage: ((event: { data: unknown }) => void) | null = null;
    onerror = null; onmessageerror = null;
    terminate = vi.fn(); postMessage = vi.fn();
    constructor() { workers.push(this); }
  }
  vi.stubGlobal('Worker', ControlledWorker);
  const { runStudyTask } = await import('../../app/lib/background-study');
  for (let generation = 1; generation <= 3; generation++) {
    const controller = new AbortController();
    const promise = runStudyTask({ kind: 'repertoireAlternatives', generation, lines: [] }, controller.signal);
    const worker = workers.at(-1)!;
    const id = worker.postMessage.mock.calls[0][0].id;
    worker.onmessage?.({ data: { id, state: 'running' } });
    const late = worker.onmessage;
    const rejected = expect(promise).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort(); await rejected;
    expect(worker.terminate).toHaveBeenCalledOnce();
    late?.({ data: { id, result: 'obsolete' } });
  }
});
