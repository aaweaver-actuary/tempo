#!/usr/bin/env node
import { MaiaWorker } from './worker.mjs';
import { inspectResources, validateResourceBudget } from './resources.mjs';
import { pathToFileURL } from 'node:url';

export const MAX_LINE_BYTES = 64 * 1024;
const MAX_BUFFER_BYTES = 2 * MAX_LINE_BYTES;

/** Framing is independently testable; production always supplies a normal MaiaWorker. */
export function serveProtocol(worker, { input, output, diagnostic = () => {} }) {
  let buffer = Buffer.alloc(0), failed = false, outputBlocked = false, processing = false, finished = false, inputEnded = false;
  let settle;
  const done = new Promise((resolve) => { settle = resolve; });
  const finish = (drain, reason = null) => {
    if (finished) { if (!drain) void worker.close({ drain: false }); return; }
    finished = true;
    if (!drain) input.destroy();
    void worker.close({ drain }).then(() => settle({ success: !failed, reason }));
  };
  const protocolFailure = (reason) => {
    if (failed || finished) return;
    failed = true; diagnostic({ event: 'protocol_error', reason }); finish(false, reason);
  };
  const emitRecord = (record) => {
    if (!output.write(`${JSON.stringify(record)}\n`)) { outputBlocked = true; input.pause(); }
  };
  worker.on('record', emitRecord);
  output.on('error', (error) => {
    diagnostic({ event: 'output_error', reason: error.code ?? 'output_failed' });
    failed = true; finish(false, error.code ?? 'output_failed');
  });
  const processBuffered = () => {
    if (processing || failed || outputBlocked || finished) return;
    processing = true;
    try {
      while (true) {
        if (outputBlocked || failed || finished) break;
        const end = buffer.indexOf(10);
        if (end < 0) { if (buffer.length > MAX_LINE_BYTES) protocolFailure('line_too_large'); break; }
        if (end > MAX_LINE_BYTES) { protocolFailure('line_too_large'); break; }
        let line;
        try { line = new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, end)); }
        catch { protocolFailure('invalid_utf8'); break; }
        buffer = buffer.subarray(end + 1);
        if (!line.trim()) { protocolFailure('empty_record'); break; }
        let record;
        try { record = JSON.parse(line); } catch { protocolFailure('invalid_json'); break; }
        try { worker.receive(record); } catch (error) { protocolFailure(error.code ?? 'invalid_record'); break; }
      }
    } finally {
      processing = false;
      if (inputEnded && !finished && !outputBlocked) {
        if (buffer.length) protocolFailure('unterminated_record');
        else finish(true);
      }
    }
  };
  output.on('drain', () => {
    outputBlocked = false; processBuffered();
    if (!outputBlocked && !failed && !finished) input.resume();
  });
  input.on('data', (chunk) => {
    if (failed || finished) return;
    if (buffer.length + chunk.length > MAX_BUFFER_BYTES) { protocolFailure('input_buffer_exceeded'); return; }
    buffer = Buffer.concat([buffer, chunk]); processBuffered();
  });
  input.on('end', () => {
    if (failed || finished) return;
    inputEnded = true; processBuffered();
  });
  input.on('error', () => { failed = true; finish(false, 'input_failed'); });
  input.on('close', () => { if (!finished && !inputEnded) finish(false, 'input_closed'); });
  input.resume();
  return { done, stop: () => finish(false, 'shutdown_requested') };
}

async function main() {
  const command = process.argv[2];
  if (process.argv.length !== 3 || !['identity', 'health', 'serve'].includes(command)) throw Object.assign(new Error('Usage: tempo-maia identity|health|serve'), { code: 'invalid_command' });
  if (command === 'identity') {
    const { inspectModel } = await import('./model.mjs');
    process.stdout.write(`${JSON.stringify(await inspectModel())}\n`);
    return;
  }
  const worker = new MaiaWorker();
  const diagnostic = (event) => process.stderr.write(`${JSON.stringify(event)}\n`);
  worker.on('diagnostic', diagnostic);
  if (command === 'health') {
    let resources, identity;
    try {
      resources = await inspectResources();
      const { inspectModel } = await import('./model.mjs');
      identity = await inspectModel();
      validateResourceBudget(null, resources);
      const health = await worker.start();
      process.stdout.write(`${JSON.stringify({ ready: true, ...health })}\n`);
    } catch (error) {
      process.stdout.write(`${JSON.stringify({ ready: false, reason: error.code ?? 'worker_start_failed', resources, ...(identity ? { identity } : {}) })}\n`);
      diagnostic({ event: 'unavailable', reason: error.code ?? 'worker_start_failed', message: error.message });
      process.exitCode = 1;
    } finally { await worker.close({ drain: false }); }
    return;
  }
  let protocol;
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { if (protocol) protocol.stop(); else void worker.close({ drain: false }); });
  await worker.start();
  protocol = serveProtocol(worker, { input: process.stdin, output: process.stdout, diagnostic });
  if (!(await protocol.done).success) process.exitCode = 1;
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ event: 'unavailable', reason: error.code ?? 'worker_start_failed', message: error.message })}\n`);
  process.exitCode = 1;
});
