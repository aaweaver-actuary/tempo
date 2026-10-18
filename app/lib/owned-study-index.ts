import type { StudyPositionTask } from './study-computation';
import { studyReplySchema } from '../domain/schemas';
import { parseData, reportDataDiagnostic } from './validated-data';
import { optionalStudyWorkPaused, subscribeStudyPriority } from './study-priority';
import { recordTempoDuration } from './performance';

type Initializer = Extract<StudyPositionTask, { kind: 'initializePositionIndex' | 'initializeComparisonIndex' }>;
type Request = { id: number; task: StudyPositionTask; queuedAt: number; signal?: AbortSignal;
  current?: () => boolean; abort: () => void; resolve: (value: unknown) => void; reject: (error: Error) => void };
type Lane = { latestRevision?: number; initializer?: Initializer; worker?: Worker; queued: Request[]; active?: Request;
  timer?: ReturnType<typeof setTimeout>; restoring?: number };
const lanes = new Map<string, Lane>();
let nextId = 0;
let runningLane: Lane | undefined;
const waitingLanes: Lane[] = [];
const keyFor = (task: StudyPositionTask) => task.kind.includes('Comparison') ? 'comparison' : `repertoire:${'repertoireId' in task ? task.repertoireId : ''}`;
const isInitializer = (task: StudyPositionTask): task is Initializer => task.kind.startsWith('initialize');

function stop(lane: Lane) {
  if(runningLane === lane) runningLane = undefined;
  clearTimeout(lane.timer); lane.timer = undefined; lane.restoring = undefined;
  if (!lane.worker) return;
  lane.worker.onmessage = null; lane.worker.onerror = null; lane.worker.onmessageerror = null;
  lane.worker.terminate(); lane.worker = undefined;
}
function settle(request: Request, error?: Error, result?: unknown) {
  request.signal?.removeEventListener('abort', request.abort);
  if (error) request.reject(error); else request.resolve(result);
}
function fail(lane: Lane, cause: unknown) {
  stop(lane);
  const requests = [...(lane.active ? [lane.active] : []), ...lane.queued];
  lane.active = undefined; lane.queued = [];
  for (const request of requests) settle(request,
    new Error('Study position index failed. Reopen its workspace to retry.', { cause }));
  startNextLane();
}
function pump(lane: Lane) {
  if(lane.queued.length && !lane.active && !waitingLanes.includes(lane)) waitingLanes.push(lane);
  startNextLane();
}
function startNextLane() {
  if(runningLane || optionalStudyWorkPaused())return;
  while(waitingLanes.length) {
    const lane=waitingLanes.shift()!;
    if(!lane.queued.length || lane.active)continue;
    runningLane=lane;startLane(lane);return;
  }
}
function startLane(lane: Lane) {
  if (optionalStudyWorkPaused() || lane.restoring !== undefined || lane.active) return;
  const request = lane.queued.shift();
  if (!request) return;
  lane.active = request;
  try {
    if (!lane.worker) {
      const worker = new Worker(new URL('./study.worker.ts', import.meta.url), { type: 'module', name: 'tempo-owned-position-index' });
      lane.worker = worker;
      worker.onmessage = ({ data }) => {
        if (lane.worker !== worker) return;
        try {
          const reply = parseData(studyReplySchema, data, 'owned study index response', undefined, 'study-worker');
          if (reply.state === 'running') return;
          if (lane.restoring === reply.id) {
            if (reply.error) throw new Error(reply.error);
            lane.restoring = undefined;
            if (lane.active) worker.postMessage({ id: lane.active.id, task: lane.active.task });
            return;
          }
          const current = lane.active;
          if (!current || current.id !== reply.id) throw new Error('Position worker returned an unrelated request');
          if (reply.error) throw new Error(reply.error);
          for (const diagnostic of reply.diagnostics ?? []) {
            if (current.current?.() === false) break;
            reportDataDiagnostic(diagnostic.source, diagnostic.raw, diagnostic.message, diagnostic.recordId);
          }
          clearTimeout(lane.timer); lane.timer = undefined;
          if (isInitializer(current.task)) lane.initializer = current.task;
          lane.active = undefined; runningLane = undefined;
          recordTempoDuration('study-worker-roundtrip', performance.now() - current.queuedAt);
          settle(current, undefined, reply.result); pump(lane);
        } catch (error) { fail(lane, error); }
      };
      worker.onerror = event => { if (lane.worker === worker) fail(lane, event.message); };
      worker.onmessageerror = () => { if (lane.worker === worker) fail(lane, 'Position response could not be decoded'); };
      if (lane.initializer && !isInitializer(request.task)) {
        lane.restoring = ++nextId;
        worker.postMessage({ id: lane.restoring, task: lane.initializer });
      }
    }
    lane.timer = setTimeout(() => fail(lane, 'Position preparation timed out after 120 seconds'), 120_000);
    if (lane.restoring === undefined) lane.worker.postMessage({ id: request.id, task: request.task });
  } catch (error) { fail(lane, error); }
}

subscribeStudyPriority(() => {
  for (const lane of lanes.values()) {
    if (optionalStudyWorkPaused()) {
      if (lane.active) { stop(lane);lane.queued.unshift(lane.active); lane.active = undefined; }
    } else pump(lane);
  }
});

export function runOwnedStudyIndex<T>(task: StudyPositionTask, signal?: AbortSignal, current?: () => boolean): Promise<T> {
  if (signal?.aborted) return Promise.reject(new DOMException('Cancelled', 'AbortError'));
  const key = keyFor(task);
  const lane = lanes.get(key) ?? { queued: [] };
  lanes.set(key, lane);
  if (task.kind.startsWith('release')) {
    const requests = [...(lane.active ? [lane.active] : []), ...lane.queued].filter(request=>request.task.revision===task.revision);
    const ownsInitializer = lane.initializer?.revision === task.revision;
    if (!ownsInitializer && !requests.length) return Promise.resolve(false as T);
    if (lane.active && requests.includes(lane.active)) { stop(lane); lane.active = undefined; }
    lane.queued = lane.queued.filter(request=>!requests.includes(request));
    for(const request of requests) settle(request,new DOMException('Cancelled','AbortError'));
    if(ownsInitializer) lane.initializer = undefined;
    if(!lane.initializer && !lane.active && !lane.queued.length) {stop(lane);lanes.delete(key);}
    else pump(lane);
    startNextLane();
    return Promise.resolve(true as T);
  }
  if (isInitializer(task)) {
    if ((lane.latestRevision ?? -1) > task.revision)
      return Promise.reject(new Error('A newer position index is already active.'));
    lane.latestRevision = task.revision;
  }
  return new Promise((resolve, reject) => {
    const request: Request = { id: ++nextId, task, signal, current, queuedAt: performance.now(),
      resolve: value => resolve(value as T), reject, abort: () => {
        if (lane.active === request) { stop(lane); lane.active = undefined; }
        else { const index = lane.queued.indexOf(request); if (index >= 0) lane.queued.splice(index, 1); }
        settle(request, new DOMException('Cancelled', 'AbortError')); pump(lane);
      } };
    signal?.addEventListener('abort', request.abort, { once: true });
    lane.queued.push(request); pump(lane);
  });
}
