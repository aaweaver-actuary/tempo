import { EventEmitter } from 'node:events';
import { fork } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { inspectResources, validateResourceBudget } from './resources.mjs';
import { fingerprintJob, identityOf, validatePayload, validateRecord, validateResultForJob } from './wire.mjs';

export const SESSION_RECORD_LIMIT = 1024;
export const CANCELLATION_RECORD_LIMIT = 64;

function logicalKey(payload) { return JSON.stringify([payload.workspace_id, payload.job_id, payload.request_revision]); }
function executionKey(payload) { return JSON.stringify([payload.workspace_id, payload.job_id, payload.request_revision, payload.execution_generation]); }
function productionChild() {
  return fork(new URL('./child.mjs', import.meta.url), [], { stdio: ['ignore', 'ignore', 'pipe', 'ipc'] });
}
function errorCode(error, fallback) { return typeof error?.code === 'string' && error.code.length > 0 && error.code.length <= 200 ? error.code : fallback; }

/** One prewarmed execution process; bounded process-local receipts, no durable queue. */
export class MaiaWorker extends EventEmitter {
  constructor({ childFactory = productionChild, resourceInspector = inspectResources, recordLimit = SESSION_RECORD_LIMIT, startupTimeoutMs = 120000 } = {}) {
    super();
    this.childFactory = childFactory;
    this.resourceInspector = resourceInspector;
    this.recordLimit = recordLimit;
    this.startupTimeoutMs = startupTimeoutMs;
    this.logicalRequests = new Map();
    this.executions = new Map();
    this.child = null;
    this.childReady = false;
    this.identity = null;
    this.active = null;
    this.replacement = null;
    this.resources = null;
    this.closing = false;
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
        const error = Object.assign(new Error('Model initialization deadline exceeded'), { code: 'model_initialization_timeout' });
        if (!settled) { settled = true; reject(error); }
        child.kill('SIGKILL');
      }, this.startupTimeoutMs);
      child.stderr?.on('data', (chunk) => this.emit('diagnostic', { event: 'child_diagnostic', message: chunk.toString() }));
      child.on('message', (message) => {
        if (this.child !== child || !message || typeof message !== 'object') return;
        if (message.type === 'ready' && !settled) {
          const identity = message.identity;
          if (!identity || ['model_id', 'model_version', 'runtime_name', 'runtime_version', 'implementation_version'].some((field) => typeof identity[field] !== 'string' || identity[field].length < 1 || identity[field].length > 200)) {
            settled = true; clearTimeout(startupTimer); reject(Object.assign(new Error('Invalid model identity'), { code: 'model_provenance_mismatch' })); child.kill('SIGKILL'); return;
          }
          settled = true; clearTimeout(startupTimer); this.identity = identity; this.childReady = true;
          child.maiaInitialized = true;
          this.emit('diagnostic', { event: 'ready', identity, resources: this.resources, pid: child.pid });
          resolve();
          if (this.replacement) {
            const replacement = this.replacement; this.replacement = null;
            this.runRecord(replacement, true);
          }
          return;
        }
        if (message.type === 'startup_error' && !settled) {
          settled = true; clearTimeout(startupTimer); reject(Object.assign(new Error(message.message ?? 'Model failed to load'), { code: errorCode(message, 'model_loading_failed') }));
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
        if (!settled) { settled = true; reject(Object.assign(new Error(`Model process exited during initialization (${code ?? signal})`), { code: 'model_loading_failed' })); }
        this.onChildExit(child, code, signal);
      });
    }).finally(() => { this.spawnPromise = null; });
    return this.spawnPromise;
  }

  emitRecord(model, payload) {
    validatePayload(model, payload);
    this.emit('record', { model, payload });
  }

  status(record, status, reason = null, retryable = false) {
    const payload = { ...identityOf(record.identity), status, retryable, reason };
    record.status = payload;
    this.emitRecord('JobStatus', payload);
  }

  acknowledge(request, status) {
    this.emitRecord('CancellationAcknowledgement', { ...request, status, acknowledged_at: new Date().toISOString() });
  }

  rememberCancellation(record, request) {
    record.cancellations ??= new Map();
    if (!record.cancellations.has(request.cancellation_id) && record.cancellations.size >= CANCELLATION_RECORD_LIMIT) { this.rejectJob(request, 'cancellation_capacity_exhausted', true); return false; }
    record.cancellations.set(request.cancellation_id, request);
    record.cancellation ??= request;
    return true;
  }

  acknowledgeTermination(record) {
    for (const request of record.cancellations?.values() ?? []) this.acknowledge(request, 'terminated');
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
    if (input.model === 'MaiaJob') this.receiveJob(structuredClone(input.payload));
    else this.receiveCancellation(structuredClone(input.payload));
  }

  receiveJob(job) {
    const key = logicalKey(job), generationKey = executionKey(job), fingerprint = fingerprintJob(job);
    let logical = this.logicalRequests.get(key), record = this.executions.get(generationKey);
    if (logical?.fingerprint && logical.fingerprint !== fingerprint) { this.rejectJob(job, 'identity_payload_conflict'); return; }
    if (logical && job.execution_generation < logical.highestGeneration) { this.rejectJob(job, 'stale_generation'); return; }
    if (record?.job) {
      if (record.result) this.emitRecord('MaiaResult', record.result);
      if (record.status) this.emitRecord('JobStatus', record.status);
      return;
    }
    if (!record && this.executions.size >= this.recordLimit) { this.rejectJob(job, 'session_capacity_exhausted', true); return; }
    if (!logical) { logical = { fingerprint: null, highestGeneration: 0 }; this.logicalRequests.set(key, logical); }
    logical.fingerprint = fingerprint;
    logical.highestGeneration = Math.max(logical.highestGeneration, job.execution_generation);
    if (!record) { record = { identity: identityOf(job), job: null, status: null, result: null, cancellation: null }; this.executions.set(generationKey, record); }
    record.job = job;
    if (record.cancellation) {
      this.status(record, 'cancelled', 'cancelled_before_execution');
      this.acknowledgeTermination(record);
      if (this.active && logicalKey(this.active.record.identity) === key && this.active.record.identity.execution_generation < job.execution_generation) this.stopActive({ status: 'failed', reason: 'stale_generation', retryable: false });
      return;
    }
    try { validateResourceBudget(job.constraints, this.resources); }
    catch (error) { this.status(record, 'failed', errorCode(error, 'memory_budget_unsupported'), false); return; }
    if (this.identity && (job.model_id !== this.identity.model_id || job.model_version !== this.identity.model_version)) { this.status(record, 'failed', 'unsupported_model'); return; }
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
    this.active.dispatched = true;
    this.child.send({ type: 'infer', token, job }, (error) => { if (error && this.active?.token === token) this.stopActive({ status: 'failed', reason: 'worker_crashed', retryable: true }); });
  }

  receiveCancellation(request) {
    const key = logicalKey(request), generationKey = executionKey(request);
    const logical = this.logicalRequests.get(key);
    if (logical && request.execution_generation < logical.highestGeneration) { this.acknowledge(request, 'stale_generation'); return; }
    let record = this.executions.get(generationKey);
    if (!record) {
      if (this.executions.size >= this.recordLimit) { this.rejectJob(request, 'session_capacity_exhausted', true); return; }
      if (!logical) this.logicalRequests.set(key, { fingerprint: null, highestGeneration: 0 });
      record = { identity: identityOf(request), job: null, status: null, result: null, cancellation: null };
      this.executions.set(generationKey, record);
      this.rememberCancellation(record, request);
      this.acknowledge(request, 'requested');
      return;
    }
    if (record.result) { this.acknowledge(request, 'already_completed'); return; }
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
      this.acknowledge(request, 'requested');
      this.stopActive({ status: 'cancelled', reason: 'cancelled_by_request', retryable: false });
      return;
    }
    this.acknowledge(request, 'terminated');
  }

  handleChildMessage(child, message) {
    const active = this.active;
    if (!active || active.child !== child || active.token !== message.token || active.stopping) return;
    if (performance.now() >= active.deadlineAt) { this.stopActive({ status: 'failed', reason: 'execution_timeout', retryable: true }); return; }
    const record = active.record;
    if (message.type === 'started') {
      this.status(record, 'running');
      this.emit('diagnostic', { event: 'execution_started', ...identityOf(record.identity), pid: child.pid, started_at: active.startedAt });
      return;
    }
    if (message.type === 'result') {
      const result = {
        ...identityOf(record.identity), worker: 'maia', model_id: this.identity.model_id, model_version: this.identity.model_version,
        provenance: { input_digest: record.job.input_digest, runtime_name: this.identity.runtime_name, runtime_version: this.identity.runtime_version, implementation_version: this.identity.implementation_version, started_at: active.startedAt, finished_at: new Date().toISOString() },
        predictions: message.predictions,
      };
      try { validateResultForJob(record.job, result); }
      catch { this.stopActive({ status: 'failed', reason: 'invalid_model_output', retryable: false }); return; }
      record.result = result;
      clearTimeout(active.timer); this.active = null; this.childReady = true;
      this.emitRecord('MaiaResult', result); this.status(record, 'completed'); this.emit('idle');
    } else if (message.type === 'failure') {
      clearTimeout(active.timer); this.active = null; this.childReady = true;
      this.status(record, 'failed', errorCode(message, 'inference_failed'), message.retryable === true); this.emit('idle');
    }
  }

  stopActive(outcome) {
    if (!this.active || this.active.stopping) return;
    this.active.stopping = outcome;
    clearTimeout(this.active.timer);
    this.active.child.kill('SIGKILL');
  }

  onChildExit(child, code, signal) {
    if (this.child !== child) return;
    this.child = null; this.childReady = false;
    const active = this.active;
    if (active?.child === child) {
      clearTimeout(active.timer); this.active = null;
      const outcome = active.stopping ?? { status: 'failed', reason: 'worker_crashed', retryable: true };
      this.status(active.record, outcome.status, outcome.reason, outcome.retryable);
      this.acknowledgeTermination(active.record);
      this.emit('idle');
    }
    this.emit('diagnostic', { event: 'child_exited', pid: child.pid, code, signal });
    this.emit('child_exit');
    if ((!this.closing && child.maiaInitialized) || this.replacement) {
      // The exit event can fire before the old prewarm promise's finally handler.
      setImmediate(() => {
        if ((!this.closing || this.replacement) && !this.child) this.prewarm().catch((error) => {
          if (this.replacement) { const replacement = this.replacement; this.replacement = null; clearTimeout(replacement.timer); this.status(replacement, 'failed', errorCode(error, 'model_loading_failed')); this.emit('idle'); }
          this.emit('diagnostic', { event: 'unavailable', reason: errorCode(error, 'model_loading_failed'), message: error.message });
        });
      });
    }
  }

  close({ drain = true } = {}) {
    if (this.closePromise) {
      if (!drain) {
        if (this.replacement) { const replacement = this.replacement; this.replacement = null; clearTimeout(replacement.timer); this.status(replacement, 'failed', 'execution_interrupted', true); }
        this.stopActive({ status: 'failed', reason: 'execution_interrupted', retryable: true });
      }
      return this.closePromise;
    }
    this.closing = true;
    this.closePromise = (async () => {
      if (!drain && this.replacement) { const replacement = this.replacement; this.replacement = null; clearTimeout(replacement.timer); this.status(replacement, 'failed', 'execution_interrupted', true); }
      while (this.active || this.replacement) {
        const idle = new Promise((resolve) => this.once('idle', resolve));
        if (!drain) this.stopActive({ status: 'failed', reason: 'execution_interrupted', retryable: true });
        await idle;
      }
      if (this.child) {
        const child = this.child;
        await new Promise((resolve) => { child.once('exit', resolve); child.kill('SIGKILL'); });
      }
      this.closed = true;
    })();
    return this.closePromise;
  }
}
