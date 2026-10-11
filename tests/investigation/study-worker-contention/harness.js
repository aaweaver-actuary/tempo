import { runStudyTask } from '../../../app/lib/background-study';
import { readWorkspaceData, invalidateWorkspaceData } from '../../../app/lib/workspace-data';
import { tempoPerformanceTimings } from '../../../app/lib/performance';
import { canonicalFenKey } from '../../../app/utils/canonical-line';

const NativeWorker = window.Worker;
const workers = [];
const requests = new Map();
const longTasks = [];
const responsePhases = [];
let startHook;
let preparedLines;
let foregroundTasks;
const now = () => performance.timeOrigin + performance.now();
if (window.__investigationTraced) {
  const originalFetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    const startedAt = now();
    const response = await originalFetch(...args);
    responsePhases.push({ name: 'fetch-to-headers', startedAt, durationMs: now() - startedAt });
    return response;
  };
  const originalJson = Response.prototype.json;
  Response.prototype.json = async function () {
    const startedAt = now();
    const result = await originalJson.call(this);
    responsePhases.push({ name: 'response-body-and-json', startedAt, durationMs: now() - startedAt });
    return result;
  };
}
new PerformanceObserver(list => longTasks.push(...list.getEntries().map(entry => ({ start: performance.timeOrigin + entry.startTime, duration: entry.duration })))).observe({ type: 'longtask', buffered: true });

class ObservedWorker extends NativeWorker {
  constructor(...args) {
    super(...args);
    workers.push(this);
    this.addEventListener('message', event => {
      const trace = event.data.__investigation;
      delete event.data.__investigation;
      const record = requests.get(event.data.id);
      if (!record) return;
      if (event.data.state === 'running') {
        record.runningAt = now();
        record.workerRunningNoticeAt = trace?.at;
        record.workerReceiptAt = trace?.receivedAt;
        record.workerValidatedAt = trace?.validatedAt;
        record.runningStages = trace?.stages;
        if (startHook && record.kind === 'repertoireAlternatives') {
          const hook = startHook;
          startHook = undefined;
          hook();
        }
      } else {
        record.replyAt = now();
        record.workerEndAt = trace?.at;
        record.workerStartAt = trace?.computeStartedAt;
        record.workerReceiptAt = trace?.receivedAt;
        record.workerValidatedAt = trace?.validatedAt;
        record.stages = trace?.stages;
        record.fenNormalization = trace?.fenNormalization;
        record.computeMs = event.data.computeMs;
        record.error = event.data.error;
      }
    });
  }
  postMessage(message, ...args) {
    const record = { id: message.id, kind: message.task.kind, sentAt: now() };
    requests.set(message.id, record);
    super.postMessage(message, ...args);
    record.postMs = now() - record.sentAt;
  }
}
window.Worker = ObservedWorker;

async function call(task, signal) {
  const pending = runStudyTask(task, signal);
  const record = [...requests.values()].at(-1);
  try {
    const value = await pending;
    if (['queue', 'deck', 'resolveTrainingRoute'].includes(task.kind)) {
      if (!Array.isArray(value) || !value.length) throw new Error(`Foreground ${task.kind} produced no usable result`);
      if (!value.every(result => result !== null && typeof result === 'object')) throw new Error(`Foreground ${task.kind} accepted a malformed result`);
      record.resultCount = value.length;
    }
    record.settledAt = now();
    record.outcome = 'fulfilled';
    return value;
  } catch (error) {
    record.settledAt = now();
    record.outcome = error.name;
    throw error;
  }
}

function summarizeSnapshot(snapshot) {
  const canonicalPositions = new Set();
  const positionsByRepertoire = new Map();
  let memberships = 0;
  let distinctKeys = 0;
  for (const position of snapshot.positions) {
    canonicalPositions.add(canonicalFenKey(position.fen));
    positionsByRepertoire.set(position.repertoireId, (positionsByRepertoire.get(position.repertoireId) ?? 0) + 1);
  }
  for (const positions of snapshot.movesByRepertoire.values()) {
    distinctKeys += positions.size;
    for (const moves of positions.values()) memberships += moves.size;
  }
  return { positions: snapshot.positions.length, canonicalPositions: canonicalPositions.size, repertoirePositionKeys: distinctKeys, memberships, repertoirePositionCounts: [...positionsByRepertoire.values()].sort((a,b) => a-b), indexedRepertoires: snapshot.movesByRepertoire.size };
}

function measurements(startedAt) {
  const records = [...requests.values()].filter(record => record.sentAt >= startedAt);
  const workerClockInversions = window.__investigationTraced ? records.flatMap(record => {
    const boundaries = [record.sentAt, record.workerReceiptAt, record.workerValidatedAt, record.workerStartAt, record.workerEndAt, record.replyAt];
    return boundaries.flatMap((boundary,index) => index && boundary !== undefined && boundaries[index-1] !== undefined && boundary < boundaries[index-1] - 0.2 ? [{id:record.id,boundary:index,inversionMs:boundaries[index-1]-boundary}] : []);
  }) : undefined;
  if (workerClockInversions?.length) throw new Error('Browser/worker epoch ordering failed; phase attribution is not valid');
  return { startedAt, durationMs: now() - startedAt, requests: records, workerClockInversions, responsePhases: responsePhases.filter(phase => phase.startedAt >= startedAt), longTasks: longTasks.filter(task => task.start + task.duration >= startedAt), tempoTimings: tempoPerformanceTimings().filter(timing => timing.recordedAt >= Math.floor(startedAt) - 1) };
}

async function configure() {
  const startedAt = now();
  const payload = await (await fetch('/fixture.json')).json();
  const validated = await call({ kind: 'workspace', url: '/api/repertoire/lines', payload });
  preparedLines = await call({ kind: 'transportLines', payload: validated });
  const deck = await (await fetch('/deck.json')).json();
  const canonicalLines = await call({ kind: 'lines', lines: preparedLines.slice(0, 32) });
  const startingFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  foregroundTasks = [
    { kind: 'queue', payload: { cards: Array.from({ length: 20 }, (_, index) => ({ id: `foreground-${index}`, queue_entry_id: index+1, start_fen: startingFen, moves: ['e2e4'], content_type: 'opening', repertoire_name: 'Foreground', repertoire_source: 'PGN', trained_color: 'white' })), count: 20 } },
    { kind: 'workspace', url: '/api/repertoire/lines', payload: { lines: payload.lines.slice(0,32) } },
    { kind: 'resolveTrainingRoute', repertoireId: canonicalLines[0]?.repertoireId ?? 'fixture', startingFen: canonicalLines[0]?.startingFen ?? startingFen, moves: canonicalLines[0]?.moves.slice(0,2) ?? [], lines: canonicalLines },
    { kind: 'deck', records: deck, deckId: deck[0].DeckId },
  ];
  const snapshot = await call({ kind: 'repertoireAlternatives', generation: 1, lines: preparedLines });
  const encodedMembership = [...snapshot.movesByRepertoire].map(([repertoire, positions]) => [repertoire, [...positions].map(([fen, moves]) => [fen, [...moves]])]);
  const snapshotEncoding = JSON.stringify({ ...snapshot, movesByRepertoire: encodedMembership });
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(snapshotEncoding));
  return { lines: preparedLines.length, ...summarizeSnapshot(snapshot), coldConfiguration: measurements(startedAt), outputSha256: [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join(''), payloadJsonBytes: new TextEncoder().encode(JSON.stringify({ kind: 'repertoireAlternatives', generation: 1, lines: preparedLines })).byteLength, replyJsonEquivalentBytes: new TextEncoder().encode(snapshotEncoding).byteLength, membershipOnlyJsonEquivalentBytes: new TextEncoder().encode(JSON.stringify({generation:snapshot.generation,movesByRepertoire:encodedMembership})).byteLength };
}

async function pipeline() {
  requests.clear(); longTasks.length = 0;
  invalidateWorkspaceData('/api/repertoire/lines');
  const startedAt = now();
  const payload = await readWorkspaceData('/api/repertoire/lines');
  const lines = await call({ kind: 'transportLines', payload });
  const snapshot = await call({ kind: 'repertoireAlternatives', generation: 1, lines });
  const publishedAt = now();
  window.currentSnapshot = snapshot;
  const publicationMs = now() - publishedAt;
  const result = measurements(startedAt);
  result.publicationMs = publicationMs;
  result.positionCount = snapshot.positions.length;
  return result;
}

async function contention(scenario = 'foreground', contended = true) {
  requests.clear(); longTasks.length = 0;
  const startedAt = now();
  const controller = new AbortController();
  let foreground;
  const replacements = [];
  let cancelAt;
  let cancelledAt;
  let failureAt;
  let publishedGeneration;
  let queuedFailureOutcomes;
  const startForeground = () => {
    if (contended && scenario === 'cancel') { cancelAt = now(); controller.abort(); }
    if (contended && scenario === 'invalidate') {
      controller.abort();
      let currentGeneration = 1;
      for (let generation = 2; generation <= 4; generation++) {
        currentGeneration = generation;
        invalidateWorkspaceData('/api/repertoire/lines');
        replacements.push((async () => {
          const payload = await readWorkspaceData('/api/repertoire/lines');
          if (generation !== currentGeneration) return 'obsolete-fetch';
          const lines = await call({ kind: 'transportLines', payload });
          if (generation !== currentGeneration) return 'obsolete-transport';
          const snapshot = await call({ kind: 'repertoireAlternatives', generation, lines });
          if (generation === currentGeneration) publishedGeneration = snapshot.generation;
          return generation === currentGeneration ? 'published' : 'obsolete-index';
        })());
      }
    }
    if (contended && ['failure', 'decode', 'protocol'].includes(scenario)) {
      failureAt = now();
      const failedWorker = workers.at(-1);
      const previousMessageHandler = failedWorker.onmessage;
      const previousErrorHandler = failedWorker.onerror;
      const previousDecodeHandler = failedWorker.onmessageerror;
      const queuedBeforeFailure = foregroundTasks.map(task => call(task).then(() => 'unexpected-success', error => error.name));
      if (scenario === 'failure') failedWorker.onerror(new ErrorEvent('error', { message: 'Investigation injected worker failure' }));
      else if (scenario === 'decode') failedWorker.onmessageerror(new MessageEvent('messageerror'));
      else failedWorker.onmessage(new MessageEvent('message', { data: { id: 'invalid-investigation-reply' } }));
      foreground = Promise.all(foregroundTasks.map(task => call(task)));
      const replacementId = [...requests.values()].at(-1).id;
      previousMessageHandler(new MessageEvent('message', { data: { id: replacementId, result: ['obsolete-worker-result'] } }));
      previousErrorHandler(new ErrorEvent('error', { message: 'Obsolete investigation worker callback' }));
      previousDecodeHandler(new MessageEvent('messageerror'));
      replacements.push(Promise.all(queuedBeforeFailure).then(outcomes => { queuedFailureOutcomes = outcomes; }));
      return;
    }
    foreground = Promise.all(foregroundTasks.map(task => call(task)));
  };
  let background;
  if (contended) {
    startHook = startForeground;
    background = call({ kind: 'repertoireAlternatives', generation: 1, lines: preparedLines }, controller.signal)
      .then(() => 'fulfilled', error => { cancelledAt = now(); return error.name; });
    await background;
  } else startForeground();
  await foreground;
  await Promise.allSettled(replacements);
  return { ...measurements(startedAt), scenario, contended, cancelAt, cancelledAt, failureAt, publishedGeneration, queuedFailureOutcomes, workerInstances: workers.length };
}

function collectLongTasks(start, end) { return longTasks.filter(task => task.start + task.duration >= start && task.start <= end); }
window.investigation = { configure, pipeline, contention, collectLongTasks, workers, requests, now };
