#!/usr/bin/env node
import { StockfishWorker } from './worker.mjs';
import { inspectEngine } from './engine.mjs';
import { pathToFileURL } from 'node:url';
import { realpathSync } from 'node:fs';

export const MAX_LINE_BYTES = 64 * 1024;
export const MAX_OUTPUT_BYTES = 1024 * 1024;
export const MAX_QUEUE_BYTES = 2 * MAX_OUTPUT_BYTES;

export function serveProtocol(worker, { input, output, diagnostic = () => {}, outputTimeoutMs = 5000 }) {
  let buffer = Buffer.alloc(0), blocked = false, processing = false, finished = false, failed = false, inputEnded = false, stallTimer;
  let settle;
  const done = new Promise(resolve => { settle = resolve; });
  const finish = (drain, reason = null) => {
    if (finished) { if (!drain) void worker.close({ drain: false }); return; }
    finished = true; input.pause();
    if (!drain) { input.destroy(); output.destroy(); clearTimeout(stallTimer); }
    void worker.close({ drain }).then(() => {
      if (blocked && drain) output.once('drain', () => { clearTimeout(stallTimer); settle({ success: !failed, reason }); });
      else { clearTimeout(stallTimer); settle({ success: !failed, reason }); }
    }).catch(error => { failed = true; output.destroy(); clearTimeout(stallTimer); diagnostic({ event: 'fatal', reason: error.code ?? 'shutdown_failed' }); settle({ success: false, reason: error.code ?? 'shutdown_failed' }); });
  };
  const protocolFailure = reason => { failed = true; diagnostic({ event: 'protocol_error', reason }); if (finished) { output.destroy(); clearTimeout(stallTimer); void worker.close({ drain: false }).then(() => settle({ success: false, reason })); } else finish(false, reason); };
  worker.on('record', record => {
    if (failed) return;
    const line = JSON.stringify(record) + '\n';
    if (Buffer.byteLength(line) > MAX_OUTPUT_BYTES || output.writableLength + Buffer.byteLength(line) > MAX_QUEUE_BYTES) { protocolFailure('output_capacity_exceeded'); return; }
    if (!output.write(line)) {
      blocked = true; input.pause();
      stallTimer ??= setTimeout(() => protocolFailure('output_stalled'), outputTimeoutMs);
    }
  });
  output.on('error', () => protocolFailure('output_failed'));
  const processBuffered = () => {
    if (processing || blocked || finished) return;
    processing = true;
    try {
      const end = buffer.indexOf(10);
      if (end < 0) { if (buffer.length > MAX_LINE_BYTES) protocolFailure('line_too_large'); else if (inputEnded && buffer.length) protocolFailure('unterminated_record'); else if (inputEnded) finish(true); return; }
      if (end > MAX_LINE_BYTES) { protocolFailure('line_too_large'); return; }
      const raw = buffer.subarray(0, end); buffer = buffer.subarray(end + 1);
      let record;
      try { record = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw)); }
      catch { protocolFailure('invalid_record'); return; }
      try { worker.receive(record); }
      catch { protocolFailure('invalid_record'); return; }
    } finally { processing = false; }
    if (!finished && !blocked && (buffer.length || inputEnded)) setImmediate(processBuffered);
  };
  output.on('drain', () => { blocked = false; clearTimeout(stallTimer); stallTimer = null; processBuffered(); if (!finished && !blocked) input.resume(); });
  input.on('data', chunk => {
    if (finished) return;
    if (buffer.length + chunk.length > 2 * MAX_LINE_BYTES) { protocolFailure('input_buffer_exceeded'); return; }
    buffer = Buffer.concat([buffer, chunk]); processBuffered();
  });
  input.on('end', () => { inputEnded = true; processBuffered(); });
  input.on('error', () => protocolFailure('input_failed'));
  input.on('close', () => { if (!inputEnded && !finished) finish(false, 'input_closed'); });
  input.resume();
  return { done, stop: () => finish(false, 'shutdown_requested') };
}

async function main() {
  const command = process.argv[2];
  if (process.argv.length !== 3 || !['identity', 'health', 'serve'].includes(command)) throw Object.assign(new Error('Usage: tempo-stockfish identity|health|serve'), { code: 'invalid_command' });
  if (command === 'identity') { process.stdout.write(JSON.stringify(await inspectEngine()) + '\n'); return; }
  const worker = new StockfishWorker();
  const diagnostic = event => { if (process.stderr.writableLength < 65536) process.stderr.write(JSON.stringify(event) + '\n'); };
  let protocol;
  worker.on('diagnostic', event => { diagnostic(event); if (event.event === 'fatal') protocol?.stop(); });
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { if (protocol) protocol.stop(); else void worker.close({ drain: false }); });
  try {
    const health = await worker.start();
    if (command === 'health') { process.stdout.write(JSON.stringify({ ready: true, ...health }) + '\n'); await worker.close({ drain: false }); return; }
    protocol = serveProtocol(worker, { input: process.stdin, output: process.stdout, diagnostic });
    // Owned child shutdown is confirmed before abandoning an undrainable stream.
    if (!(await protocol.done).success) process.exit(1);
  } catch (error) {
    diagnostic({ event: 'startup_failed', reason: error.code ?? 'worker_start_failed' });
    if (command === 'health') process.stdout.write(JSON.stringify({ ready: false, reason: error.code ?? 'worker_start_failed' }) + '\n');
    await worker.close({ drain: false }); process.exitCode = 1;
  }
}
if (process.argv[1] && pathToFileURL(realpathSync(process.argv[1])).href === import.meta.url) main().catch(error => { process.stderr.write(JSON.stringify({ event: 'fatal', reason: error.code ?? 'worker_failed' }) + '\n'); process.exitCode = 1; });
