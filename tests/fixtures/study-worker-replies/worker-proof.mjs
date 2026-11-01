import { build } from 'esbuild';
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const built = await build({ entryPoints: ['app/lib/study.worker.ts'], bundle: true, platform: 'browser', format: 'iife', write: false });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://localhost/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html>' }));
  await page.goto('http://localhost/');
  const results = await page.evaluate(async source => {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    const send = request => new Promise(resolve => {
      const worker = new Worker(url);
      worker.onmessage = event => { if (event.data.state !== 'running') { worker.terminate(); resolve(event.data); } };
      worker.onerror = event => { event.preventDefault(); worker.terminate(); resolve({ workerError: event.message }); };
      worker.postMessage(request);
    });
    try { return [await send({ id: 3, task: { kind: 'bad' } }), await send({ id: 'bad', task: {} }),
      await send({ id: 4, task: { kind: 'queue', payload: { cards: [], count: 0 } } })]; }
    finally { URL.revokeObjectURL(url); }
  }, built.outputFiles[0].text);
  assert.deepEqual(results[0], { id: 3, error: 'Invalid study worker request' });
  assert.ok(results[1].workerError);
  assert.equal(results[2].id, 4); assert.deepEqual(results[2].result, []);
  console.log('PASS actual_worker_malformed_request_disposition');
} finally { await browser.close(); }
