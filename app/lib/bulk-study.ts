import type { StudyTask } from './study-computation';
import { studyReplySchema } from '../domain/schemas';
import { parseData, reportDataDiagnostic } from './validated-data';
import { recordTempoDuration } from './performance';
import { reportDebugError } from './debug-reporting';
import { updateBrowserActivity } from './browser-activity';
import { optionalStudyWorkPaused, subscribeStudyPriority } from './study-priority';

type Request = {
  id: number; task: StudyTask; queuedAt: number; signal?: AbortSignal;
  current?: () => boolean; abort: () => void;
  resolve: (value: unknown) => void; reject: (error: Error) => void;
};
let nextId = 0;
const queued: Request[] = [];
let active: Request | undefined;
let worker: Worker | undefined;
let timeout: ReturnType<typeof setTimeout> | undefined;

export function isBulkStudyTask(task: StudyTask) {
  return ['repertoirePreparation', 'repertoireAlternatives', 'transportLines', 'lines', 'index',
    'matches', 'similarity'].includes(task.kind) ||
    (task.kind === 'workspace' && new URL(task.url, 'http://tempo.local').pathname === '/api/repertoire/lines');
}

function stopWorker() {
  clearTimeout(timeout); timeout = undefined;
  if (!worker) return;
  worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null;
  worker.terminate(); worker = undefined;
}
function settle(request: Request, error?: Error, value?: unknown) {
  request.signal?.removeEventListener('abort', request.abort);
  updateBrowserActivity(`bulk:${request.id}`, 'Preparing workspace data', error?.name === 'AbortError' ? 'complete'
    : error ? 'failed' : 'complete', error?.name === 'AbortError' ? 'Cancelled' : error ? 'Failed' : 'Finished');
  if (error) request.reject(error); else request.resolve(value);
}
function finish(error?: Error, value?: unknown) {
  const request = active;
  active = undefined; stopWorker();
  if (request) settle(request, error, value);
  pump();
}
function fail(cause: unknown) {
  reportDebugError(cause, { kind: 'uncaught-exception', source: 'bulk-study-worker', notify: active?.current?.() !== false });
  finish(new Error('Workspace preparation failed. Retry loading it while Tempo is idle.', { cause }));
}
function pump() {
  if (worker || optionalStudyWorkPaused()) return;
  active ??= queued.shift();
  if (!active) return;
  const request = active;
  try {
    const runningWorker = new Worker(new URL('./study.worker.ts', import.meta.url),
      { type: 'module', name: 'tempo-optional-preparation' });
    worker = runningWorker;
    const current = () => worker === runningWorker && active === request;
    timeout = setTimeout(() => { if (current()) fail(new Error('Workspace worker timed out after 120 seconds')); }, 120_000);
    runningWorker.onmessage = ({ data }) => {
      if (!current()) return;
      try {
        const reply = parseData(studyReplySchema, data, 'bulk study worker response', undefined, 'bulk-study-worker');
        if (reply.id !== request.id) throw new Error('Workspace worker returned an unrelated request');
        if (reply.state === 'running') {
          recordTempoDuration('bulk-worker-queue', performance.now() - request.queuedAt);
          updateBrowserActivity(`bulk:${request.id}`, 'Preparing workspace data', 'running', 'Computing');
          return;
        }
        if (reply.error) throw new Error(reply.error);
        for (const issue of reply.diagnostics ?? []) {
          if (request.current?.() === false) break;
          reportDataDiagnostic(issue.source, issue.raw, issue.message, issue.recordId);
        }
        if (reply.computeMs !== undefined) recordTempoDuration('bulk-worker-compute', reply.computeMs);
        recordTempoDuration('bulk-worker-roundtrip', performance.now() - request.queuedAt);
        finish(undefined, reply.result);
      } catch (error) { fail(error); }
    };
    runningWorker.onerror = event => { if (current()) fail(new Error(event.message || 'Workspace worker failed')); };
    runningWorker.onmessageerror = () => { if (current()) fail(new Error('Workspace worker response could not be decoded')); };
    runningWorker.postMessage({ id: request.id, task: request.task });
  } catch (error) { fail(error); }
}

subscribeStudyPriority(() => {
  if (optionalStudyWorkPaused()) {
    // Retain the immutable request for a quiet-period retry; stop the actual CPU work.
    if (worker) {
      stopWorker();
      if (active) updateBrowserActivity(`bulk:${active.id}`, 'Preparing workspace data', 'queued', 'Waiting for study to become idle');
    }
  } else pump();
});

export function runBulkStudyTask<T>(task: StudyTask, signal?: AbortSignal, current?: () => boolean): Promise<T> {
  if (signal?.aborted) return Promise.reject(new DOMException('Cancelled', 'AbortError'));
  return new Promise<T>((resolve, reject) => {
    const request: Request = { id: ++nextId, task, signal, current, queuedAt: performance.now(),
      resolve: value => resolve(value as T), reject, abort: () => {
        if (active === request) { active = undefined; stopWorker(); }
        else { const index = queued.indexOf(request); if (index >= 0) queued.splice(index, 1); }
        settle(request, new DOMException('Cancelled', 'AbortError'));
        pump();
      } };
    signal?.addEventListener('abort', request.abort, { once: true });
    queued.push(request);
    updateBrowserActivity(`bulk:${request.id}`, 'Preparing workspace data', 'queued', 'Waiting for optional capacity');
    pump();
  });
}
