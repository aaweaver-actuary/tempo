// Real browser Worker executing the production entry point, bundled without a server.
import { build } from 'esbuild';
import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const fixture = JSON.parse(await readFile(new URL('./producer.json', import.meta.url), 'utf8'));
const { prepared_at, ...payload } = fixture.available;
const built = await build({ entryPoints: ['app/lib/study.worker.ts'], bundle: true,
  platform: 'browser', format: 'iife', write: false });
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://localhost/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html>' }));
  await page.goto('http://localhost/');
  const result = await page.evaluate(async ({ source, payload }) => {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    const worker = new Worker(url);
    try {
      return await new Promise((resolve, reject) => {
        worker.onerror = event => reject(new Error(event.message));
        worker.onmessage = event => { if (event.data.result || event.data.error) resolve(event.data); };
        worker.postMessage({ id: 1, task: { kind: 'queue', payload } });
      });
    } finally { worker.terminate(); URL.revokeObjectURL(url); }
  }, { source: built.outputFiles[0].text, payload });
  assert.equal(result.error, undefined);
  assert.equal(result.result.length, 3);
  assert.equal(result.result[0].openingEvidenceParentAttemptId, 'original-attempt');
  assert.deepEqual(result.result[0].openingDecisionManifest, payload.cards[0].opening_decision_manifest);
  assert.deepEqual(result.result[2].studySnapshot, payload.cards[2].study_snapshot);
  assert.deepEqual(result.diagnostics, []);
  console.log('PASS actual_queue_producer_decodes_in_real_worker');
} finally { await browser.close(); }
