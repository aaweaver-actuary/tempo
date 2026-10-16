import { EventEmitter } from 'node:events';
import { fork } from 'node:child_process';
import { prepareJob, validateVariations } from './core.mjs';
import { randomUUID } from 'node:crypto';
import { inspectResources, validateResourceBudget } from './resources.mjs';
import { fingerprintJob, identityOf, validatePayload, validateRecord, validateResultForJob, stockfishInputDigest } from './wire.mjs';

export const SESSION_RECORD_LIMIT = 1024;
export const CANCELLATION_RECORD_LIMIT = 64;

function logicalKey(payload) { return JSON.stringify([payload.workspace_id, payload.job_id, payload.request_revision]); }
function executionKey(payload) { return JSON.stringify([payload.workspace_id, payload.job_id, payload.request_revision, payload.execution_generation]); }
function productionChild() {
  return fork(new URL('./child.mjs', import.meta.url), [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'], execArgv: [] });
}
function errorCode(error, fallback) { return typeof error?.code === 'string' && error.code.length > 0 && error.code.length <= 200 ? error.code : fallback; }

/** One prewarmed execution process; bounded process-local receipts, no durable queue. */
export class StockfishWorker extends EventEmitter {
  constructor({ childFactory = productionChild, resourceInspector = inspectResources, recordLimit = SESSION_RECORD_LIMIT, startupTimeoutMs = 30000, retentionLimitBytes = 64 * 1024 * 1024, terminationTimeoutMs = 2000 } = {}) {
    super();
    this.childFactory = childFactory;
    this.resourceInspector = resourceInspector;
    this.recordLimit = recordLimit;
    this.startupTimeoutMs = startupTimeoutMs;
    this.terminationTimeoutMs = terminationTimeoutMs;
    this.retentionLimitBytes = retentionLimitBytes;
    this.retainedBytes = 0;
    this.cancellationIdentities = new Map();
    this.logicalRequests = new Map();
    this.executions = new Map();
    this.child = null;
    this.childReady = false;
    this.identity = null;
    this.active = null;
    this.replacement = null;
    this.resources = null;
    this.closing = false;
    this.interrupting = false;
    this.closed = false;
    this.spawnPromise = null;
    this.closePromise = null;
  }

  async start() {
    if (this.closing) throw new Error('Worker is closing');
    this.resources = validateResourceBudget(null, await this.resourceInspector());
    try { await this.prewarm(); }
    catch (error) { await this.close({ drain: false }); throw error; }
    return { identity: this.identity, resources: this.resources };
  }

  prewarm() {
    if (this.childReady) return Promise.resolve();
    if (this.spawnPromise) return this.spawnPromise;
    this.spawnPromise = new Promise((resolve, reject) => {
      let settled = false;
      const child = this.childFactory();
      this.child = child;
      this.childReady = false;
      const startupTimer = setTimeout(() => {
        const error = Object.assign(new Error('Engine initialization deadline exceeded'), { code: 'engine_initialization_timeout' });
        if (!settled) { settled = true; reject(error); }
        child.kill('SIGKILL');
      }, this.startupTimeoutMs);
      child.stderr?.on('data', (chunk) => this.emit('diagnostic', { event: 'child_diagnostic', message: chunk.toString().slice(0, 4096) }));
      child.on('message', (message) => {
        if (this.child !== child || !message || typeof message !== 'object') return;
        if (message.type === 'ready' && !settled) {
          const identity = message.identity;
          if (!identity || ['engine_version', 'runtime_name', 'runtime_version', 'implementation_version'].some((field) => typeof identity[field] !== 'string' || identity[field].length < 1 || identity[field].length > 200)) {
            settled = true; clearTimeout(startupTimer); reject(Object.assign(new Error('Invalid engine identity'), { code: 'engine_provenance_mismatch' })); child.kill('SIGKILL'); return;
          }
          settled = true; clearTimeout(startupTimer); this.identity = identity; this.childReady = true;
          child.stockfishInitialized = true;
          this.emit('diagnostic', { event: 'ready', identity, resources: this.resources, pid: child.pid });
          resolve();
          if (this.replacement) {
            const replacement = this.replacement; this.replacement = null;
            this.runRecord(replacement, true);
          }
          return;
        }
        if (message.type === 'startup_error' && !settled) {
          settled = true; clearTimeout(startupTimer); reject(Object.assign(new Error(message.message ?? 'Engine failed to load'), { code: errorCode(message, 'engine_loading_failed') }));
          child.kill('SIGKILL'); return;
        }
        this.handleChildMessage(child, message);
      });
      child.on('error', (error) => {
        this.emit('diagnostic', { event: 'child_error', reason: errorCode(error, 'worker_crashed'), message: error.message });
        if (!settled) { settled = true; clearTimeout(startupTimer); reject(error); }
      });
      child.once('exit', (code, signal) => {
        clearTimeout(startupTimer);
        if (!settled) { settled = true; reject(Object.assign(new Error(`Engine process exited during initialization (${code ?? signal})`), { code: 'engine_loading_failed' })); }
        this.onChildExit(child, code, signal);
      });
    }).finally(() => { this.spawnPromise = null; });
    return this.spawnPromise;
  }

  reserve(record, bytes) {
    if (this.retainedBytes + bytes > this.retentionLimitBytes) return false;
    this.retainedBytes += bytes; record.reservedBytes = (record.reservedBytes ?? 0) + bytes;
    return true;
  }

  releaseReservation(record) {
    const retained = Buffer.byteLength(JSON.stringify({ job: record.job, result: record.result, status: record.status, cancellations: [...(record.cancellations?.values() ?? [])] })) + 65536;
    const released = Math.max(0, (record.reservedBytes ?? 0) - retained);
    record.reservedBytes -= released; this.retainedBytes -= released;
  }

  emitRecord(model, payload) {
    validatePayload(model, payload);
    this.emit('record', { model, payload });
  }

  setStatus(record, status, reason = null, retryable = false) {
    if (this.isTerminal(record)) return null;
    const payload = { ...identityOf(record.identity), status, retryable, reason };
    record.status = payload;
    if (['completed', 'failed', 'cancelled'].includes(status)) this.releaseReservation(record);
    return payload;
  }

  status(record, status, reason = null, retryable = false) {
    const payload = this.setStatus(record, status, reason, retryable);
    if (payload) this.emitRecord('JobStatus', payload);
  }

  isTerminal(record) { return ['completed', 'failed', 'cancelled'].includes(record.status?.status); }

  isCurrent(record) {
    return record.identity.execution_generation === this.logicalRequests.get(logicalKey(record.identity))?.highestGeneration;
  }

  acknowledge(request, status) {
    this.emitRecord('CancellationAcknowledgement', { ...request, status, acknowledged_at: new Date().toISOString() });
  }

  rememberCancellation(record, request) {
    record.cancellations ??= new Map();
    if (!record.cancellations.has(request.cancellation_id) && record.cancellations.size >= CANCELLATION_RECORD_LIMIT) { this.rejectJob(request, 'cancellation_capacity_exhausted', true); return false; }
    const cancellationKey = JSON.stringify([request.workspace_id, request.cancellation_id]);
    const target = executionKey(request);
    if (this.cancellationIdentities.has(cancellationKey) && this.cancellationIdentities.get(cancellationKey) !== target) { this.rejectJob(request, 'cancellation_id_conflict'); return false; }
    if (!record.cancellations.has(request.cancellation_id) && !this.reserve(record, 4096)) { this.rejectJob(request, 'session_capacity_exhausted', true); return false; }
    this.cancellationIdentities.set(cancellationKey, target);
    record.cancellations.set(request.cancellation_id, request);
    record.cancellation ??= request;
    return true;
  }

  acknowledgeTermination(record) {
    for (const request of this.settleCancellations(record, 'terminated')) this.acknowledge(request, 'terminated');
  }

  settleCancellations(record, outcome) {
    // Mark every retained ID before emitting: listeners may synchronously reenter.
    record.cancellationOutcomes ??= new Map();
    const outstanding = [];
    for (const request of record.cancellations?.values() ?? []) {
      if (record.cancellationOutcomes.has(request.cancellation_id)) continue;
      record.cancellationOutcomes.set(request.cancellation_id, outcome);
      outstanding.push(request);
    }
    return outstanding;
  }

  advanceGeneration(logical, record) {
    const generation = record.identity.execution_generation;
    if (generation <= logical.highestGeneration) return false;
    const key = logicalKey(record.identity), notifications = [];
    logical.highestGeneration = generation;
    const replacingPending = this.replacement && logicalKey(this.replacement.identity) === key;
    if (replacingPending) {
      clearTimeout(this.replacement.timer);
      this.replacement = null;
    }
    // Complete the state transition before any observable status/ack callbacks.
    for (const older of this.executions.values()) {
      if (logicalKey(older.identity) !== key || older.identity.execution_generation >= generation) continue;
      if (older === this.active?.record) continue;
      if (older.job) {
        const payload = this.setStatus(older, 'failed', 'stale_generation');
        if (payload) notifications.push(() => this.emitRecord('JobStatus', payload));
      } else {
        for (const request of this.settleCancellations(older, 'stale_generation')) notifications.push(() => this.acknowledge(request, 'stale_generation'));
      }
    }
    if (this.active && logicalKey(this.active.record.identity) === key && this.active.record.identity.execution_generation < generation) {
      this.stopActive({ status: 'failed', reason: 'stale_generation', retryable: false });
    }
    for (const notify of notifications) notify();
    if (!this.active && !this.replacement) this.emit('idle');
    return Boolean(replacingPending);
  }

  rejectJob(job, reason, retryable = false) {
    this.emitRecord('JobStatus', { ...identityOf(job), status: 'failed', retryable, reason });
  }

  receive(input) {
    try { validateRecord(input, { input: true }); }
    catch (error) {
      const identity = input?.payload && identityOf(input.payload);
      try {
        validatePayload('JobStatus', { ...identity, status: 'failed', retryable: false, reason: errorCode(error, 'invalid_request') });
        this.rejectJob(identity, errorCode(error, 'invalid_request'));
      } catch {
        this.emit('diagnostic', { event: 'protocol_error', reason: errorCode(error, 'invalid_request'), message: error.message });
        throw error;
      }
      return;
    }
    if (this.closing) { this.rejectJob(input.payload, 'worker_shutting_down', true); return; }
    if (input.model === 'StockfishJob') this.receiveJob(structuredClone(input.payload));
    else this.receiveCancellation(structuredClone(input.payload));
  }

  receiveJob(job) {
    const key = logicalKey(job), generationKey = executionKey(job), fingerprint = fingerprintJob(job);
    let logical = this.logicalRequests.get(key), record = this.executions.get(generationKey);
    if (logical?.fingerprint && logical.fingerprint !== fingerprint) { this.rejectJob(job, 'identity_payload_conflict'); return; }
    if (logical && job.execution_generation < logical.highestGeneration) { this.rejectJob(job, 'stale_generation'); return; }
    if (record?.job) {
      if (record.result) this.emitRecord('StockfishResult', record.result);
      if (record.status) this.emitRecord('JobStatus', record.status);
      return;
    }
    try {
      prepareJob(job);
      if (stockfishInputDigest(job) !== job.input_digest) throw Object.assign(new Error('Digest mismatch'), { code: 'input_digest_mismatch' });
      validateResourceBudget(job.constraints, this.resources);
      if (this.identity && job.engine_version !== this.identity.engine_version) throw Object.assign(new Error('Unsupported engine/profile'), { code: 'unsupported_engine' });
    } catch (error) { this.rejectJob(job, errorCode(error, 'invalid_request')); return; }
    if (!record && this.executions.size >= this.recordLimit) { this.rejectJob(job, 'session_capacity_exhausted', true); return; }
    const candidate = record ?? { identity: identityOf(job), job: null, status: null, result: null, cancellation: null };
    if (!this.reserve(candidate, 1024 * 1024 + (record ? 0 : 65536))) { this.rejectJob(job, 'session_capacity_exhausted', true); return; }
    if (!logical) { logical = { fingerprint: null, highestGeneration: 0 }; this.logicalRequests.set(key, logical); }
    logical.fingerprint = fingerprint;
    if (!record) { record = candidate; this.executions.set(generationKey, record); }
    record.job = job;
    const replacingPending = this.advanceGeneration(logical, record);
    if (!this.isCurrent(record) || this.isTerminal(record)) return;
    if (this.closing) { this.status(record, 'failed', 'execution_interrupted', true); return; }
    if (record.cancellation) {
      this.status(record, 'cancelled', 'cancelled_before_execution');
      this.acknowledgeTermination(record);
      return;
    }
    if (this.active) {
      if (logicalKey(this.active.record.identity) === key && this.active.record.identity.execution_generation < job.execution_generation) {
        this.queueReplacement(record);
        this.stopActive({ status: 'failed', reason: 'stale_generation', retryable: false });
        return;
      }
      this.status(record, 'failed', 'worker_busy', true);
      return;
    }
    if (this.replacement) {
      if (logicalKey(this.replacement.identity) === key && this.replacement.identity.execution_generation < job.execution_generation) this.queueReplacement(record);
      else this.status(record, 'failed', 'worker_busy', true);
      return;
    }
    if (!this.childReady && replacingPending) { this.queueReplacement(record); return; }
    if (!this.childReady) { this.status(record, 'failed', 'worker_not_ready', true); return; }
    this.runRecord(record);
  }

  queueReplacement(record) {
    if (this.replacement) { clearTimeout(this.replacement.timer); this.status(this.replacement, 'failed', 'stale_generation'); }
    this.replacement = record;
    record.startedAt = new Date().toISOString();
    record.deadlineAt = performance.now() + record.job.constraints.max_wall_time_ms;
    record.timer = setTimeout(() => {
      if (this.replacement !== record) return;
      this.replacement = null; this.status(record, 'failed', 'execution_timeout', true);
      if (!this.active) this.emit('idle');
    }, record.job.constraints.max_wall_time_ms);
    this.status(record, 'pending');
  }

  runRecord(record, pendingAlready = false) {
    if (this.isTerminal(record)) return;
    if (!this.isCurrent(record)) { this.status(record, 'failed', 'stale_generation'); return; }
    if (this.interrupting || this.closed) { this.status(record, 'failed', 'execution_interrupted', true); return; }
    const job = record.job;
    const token = randomUUID(), startedAt = record.startedAt ?? new Date().toISOString();
    const deadlineAt = record.deadlineAt ?? performance.now() + job.constraints.max_wall_time_ms;
    this.active = { record, token, child: this.child, startedAt, deadlineAt, stopping: null, timer: null, dispatched: false };
    this.childReady = false;
    clearTimeout(record.timer);
    const remaining = Math.max(0, deadlineAt - performance.now());
    this.active.timer = setTimeout(() => this.stopActive({ status: 'failed', reason: 'execution_timeout', retryable: true }), remaining);
    if (!pendingAlready) this.status(record, 'pending');
    if (this.active?.token !== token || this.active.stopping) return;
    if (this.interrupting) { this.stopActive({ status: 'failed', reason: 'execution_interrupted', retryable: true }); return; }
    if (!this.isCurrent(record)) { this.stopActive({ status: 'failed', reason: 'stale_generation', retryable: false }); return; }
    if (performance.now() >= deadlineAt) { this.stopActive({ status: 'failed', reason: 'execution_timeout', retryable: true }); return; }
    this.active.dispatched = true;
    this.child.send({ type: 'search', token, job }, (error) => { if (error && this.active?.token === token) this.stopActive({ status: 'failed', reason: 'worker_crashed', retryable: true }); });
  }

  receiveCancellation(request) {
    const key = logicalKey(request), generationKey = executionKey(request);
    const logical = this.logicalRequests.get(key);
    const cancellationKey = JSON.stringify([request.workspace_id, request.cancellation_id]);
    if (this.cancellationIdentities.has(cancellationKey) && this.cancellationIdentities.get(cancellationKey) !== generationKey) { this.rejectJob(request, 'cancellation_id_conflict'); return; }
    let record = this.executions.get(generationKey);
    if (!record) {
      if (this.executions.size >= this.recordLimit) { this.rejectJob(request, 'session_capacity_exhausted', true); return; }
      record = { identity: identityOf(request), job: null, status: null, result: null, cancellation: null };
      if (!this.reserve(record, 65536)) { this.rejectJob(request, 'session_capacity_exhausted', true); return; }
      this.executions.set(generationKey, record);
      if (!this.rememberCancellation(record, request)) return;
      if (logical && request.execution_generation < logical.highestGeneration) { this.settleCancellations(record, 'stale_generation'); this.acknowledge(request, 'stale_generation'); }
      else this.acknowledge(request, 'requested');
      return;
    }
    const cancellationOutcome = record.cancellationOutcomes?.get(request.cancellation_id);
    if (cancellationOutcome) { this.acknowledge(request, cancellationOutcome); return; }
    if (logical && request.execution_generation < logical.highestGeneration) { if (this.rememberCancellation(record, request)) { this.settleCancellations(record, 'stale_generation'); this.acknowledge(request, 'stale_generation'); } return; }
    if (record.result) { if (this.rememberCancellation(record, request)) { this.settleCancellations(record, 'already_completed'); this.acknowledge(request, 'already_completed'); } return; }
    if (!record.job) { if (this.rememberCancellation(record, request)) this.acknowledge(request, 'requested'); return; }
    if (this.replacement === record) {
      if (!this.rememberCancellation(record, request)) return;
      clearTimeout(record.timer); this.replacement = null;
      this.status(record, 'cancelled', 'cancelled_before_execution'); this.acknowledgeTermination(record);
      if (!this.active) this.emit('idle');
      return;
    }
    if (this.active?.record === record) {
      if (!this.rememberCancellation(record, request)) return;
      if (!this.active.dispatched) {
        clearTimeout(this.active.timer); this.active = null; this.childReady = true;
        this.status(record, 'cancelled', 'cancelled_before_execution'); this.acknowledgeTermination(record); this.emit('idle');
        return;
      }
      // Fence output before acknowledgement listeners can synchronously reenter.
      this.stopActive({ status: 'cancelled', reason: 'cancelled_by_request', retryable: false });
      this.acknowledge(request, 'requested');
      return;
    }
    if (!this.isTerminal(record)) {
      if (!this.rememberCancellation(record, request)) return;
      this.status(record, 'cancelled', 'cancelled_before_execution');
      this.acknowledgeTermination(record);
      return;
    }
    if (this.rememberCancellation(record, request)) { this.settleCancellations(record, 'terminated'); this.acknowledge(request, 'terminated'); }
  }

  handleChildMessage(child, message) {
    const active = this.active;
    if (!active || active.child !== child || active.token !== message.token || active.stopping) return;
    if (this.interrupting) { this.stopActive({ status: 'failed', reason: 'execution_interrupted', retryable: true }); return; }
    if (this.isTerminal(active.record)) { this.stopActive(active.record.status); return; }
    if (!this.isCurrent(active.record)) { this.stopActive({ status: 'failed', reason: 'stale_generation', retryable: false }); return; }
    if (performance.now() >= active.deadlineAt) { this.stopActive({ status: 'failed', reason: 'execution_timeout', retryable: true }); return; }
    const record = active.record;
    if (message.type === 'started') {
      this.status(record, 'running');
      this.emit('diagnostic', { event: 'execution_started', ...identityOf(record.identity), pid: child.pid, started_at: active.startedAt });
      return;
    }
    if (message.type === 'result') {
      const result = {
        ...identityOf(record.identity), worker: 'stockfish', engine_version: this.identity.engine_version,
        provenance: { input_digest: record.job.input_digest, runtime_name: this.identity.runtime_name, runtime_version: this.identity.runtime_version, implementation_version: this.identity.implementation_version, started_at: active.startedAt, finished_at: new Date().toISOString() },
        variations: message.variations,
      };
      try { validateResultForJob(record.job, result); validateVariations(record.job, result.variations); if (Buffer.byteLength(JSON.stringify(result)) > 1024 * 1024) throw new Error('oversized result'); }
      catch { this.stopActive({ status: 'failed', reason: 'invalid_engine_output', retryable: false }); return; }
      record.result = result;
      clearTimeout(active.timer); this.active = null; this.childReady = true;
      const completed = this.setStatus(record, 'completed');
      this.emitRecord('StockfishResult', result);
      this.emit('diagnostic', { event: 'execution_finished', ...identityOf(record.identity), ...message.metrics });
      if (completed) this.emitRecord('JobStatus', completed);
      this.emit('idle');
    } else if (message.type === 'failure') {
      this.stopActive({ status: 'failed', reason: errorCode(message, 'engine_search_failed'), retryable: message.retryable === true });
    }
  }

  stopActive(outcome) {
    if (!this.active || this.active.stopping) return;
    const active = this.active;
    active.stopping = outcome;
    clearTimeout(active.timer);
    if (!active.dispatched) {
      this.active = null; this.childReady = true;
      this.status(active.record, outcome.status, outcome.reason, outcome.retryable);
      this.acknowledgeTermination(active.record); this.emit('idle');
      return;
    }
    active.child.kill('SIGKILL');
    active.terminationTimer = setTimeout(() => { if (this.active !== active) return; this.interrupting = true; this.emit('diagnostic', { event: 'fatal', reason: 'termination_unconfirmed' }); }, this.terminationTimeoutMs);
  }

  onChildExit(child, code, signal) {
    if (this.child !== child) return;
    this.child = null; this.childReady = false;
    const active = this.active;
    if (active?.child === child) {
      clearTimeout(active.timer); clearTimeout(active.terminationTimer); this.active = null;
      const outcome = active.stopping ?? { status: 'failed', reason: 'worker_crashed', retryable: true };
      this.status(active.record, outcome.status, outcome.reason, outcome.retryable);
      this.acknowledgeTermination(active.record);
      this.emit('idle');
    }
    this.emit('diagnostic', { event: 'child_exited', pid: child.pid, code, signal });
    this.emit('child_exit');
    if (!this.interrupting && ((!this.closing && child.stockfishInitialized) || this.replacement)) {
      // The exit event can fire before the old prewarm promise's finally handler.
      setImmediate(() => {
        if (!this.interrupting && (!this.closing || this.replacement) && !this.child) this.prewarm().catch((error) => {
          if (this.replacement) { const replacement = this.replacement; this.replacement = null; clearTimeout(replacement.timer); this.status(replacement, 'failed', errorCode(error, 'engine_loading_failed')); this.emit('idle'); }
          this.emit('diagnostic', { event: 'unavailable', reason: errorCode(error, 'engine_loading_failed'), message: error.message });
        });
      });
    }
  }

  close({ drain = true } = {}) {
    if (!drain) this.interrupting = true;
    if (this.closePromise) { if (!drain) this.stopActive({ status: 'failed', reason: 'worker_shutdown', retryable: true }); return this.closePromise; }
    this.closing = true;
    this.closePromise = (async () => {
      if (drain && (this.active || this.replacement)) {
        await new Promise(resolve => {
          const timer = setTimeout(done, 5000);
          const idle = () => { if (!this.active && !this.replacement) done(); };
          const self = this;
          function done() { clearTimeout(timer); self.off('idle', idle); resolve(); }
          this.on('idle', idle);
        });
      }
      this.interrupting = true;
      if (this.replacement) { const record = this.replacement; this.replacement = null; clearTimeout(record.timer); this.status(record, 'failed', 'worker_shutdown', true); }
      this.stopActive({ status: 'failed', reason: 'worker_shutdown', retryable: true });
      if (this.child) {
        const child = this.child;
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(Object.assign(new Error('Owned child exit unconfirmed'), { code: 'termination_unconfirmed' })), this.terminationTimeoutMs);
          child.once('exit', () => { clearTimeout(timer); resolve(); });
          child.kill('SIGKILL');
        });
      }
      this.closed = true;
    })();
    return this.closePromise;
  }
}
