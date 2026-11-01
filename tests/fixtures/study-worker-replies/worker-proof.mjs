import { build } from 'esbuild';
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const built = await build({ entryPoints: ['app/lib/study.worker.ts'], bundle: true, platform: 'browser', format: 'iife', write: false });
const decoder = await build({ entryPoints: ['app/lib/study-result-decoder.ts'], bundle: true, platform: 'browser', format: 'iife', globalName: 'studyDecoder', write: false });
const fixtures = JSON.parse(await readFile('tests/fixtures/queue-wire/producer.json', 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://localhost/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html>' }));
  await page.goto('http://localhost/');
  await page.addScriptTag({ content: decoder.outputFiles[0].text });
  const results = await page.evaluate(async ({ source, fixtures }) => {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    const send = request => new Promise(resolve => {
      const worker = new Worker(url);
      worker.onmessage = event => { if (event.data.state !== 'running') { worker.terminate(); resolve(event.data); } };
      worker.onerror = event => { event.preventDefault(); worker.terminate(); resolve({ workerError: event.message }); };
      worker.postMessage(request);
    });
    try {
      const prepared = { ...fixtures.available }; delete prepared.prepared_at;
      const payloads = [prepared, fixtures.unavailable, fixtures.empty];
      const decoded = [];
      for (const [index, payload] of payloads.entries()) {
        const task = { kind: 'queue', payload };
        const reply = await send({ id: index + 5, task });
        decoded.push(studyDecoder.decodeStudyResult(task, reply.result));
      }
      return [await send({ id: 3, task: { kind: 'bad' } }), await send({ id: 'bad', task: {} }),
        await send({ id: 4, task: { kind: 'queue', payload: { cards: [], count: 0 } } }), decoded];
    }
    finally { URL.revokeObjectURL(url); }
  }, { source: built.outputFiles[0].text, fixtures });
  assert.deepEqual(results[0], { id: 3, error: 'Invalid study worker request' });
  assert.ok(results[1].workerError);
  assert.equal(results[2].id, 4); assert.deepEqual(results[2].result, []);
  assert.deepEqual(results[3].map(cards => cards.map(card => card.kind)), [['opening', 'puzzle', 'study', 'defense', 'endgame', 'middlegame'], ['opening', 'puzzle', 'study', 'defense', 'endgame', 'middlegame'], []]);
  assert.equal(results[3][0][0].openingEvidenceParentAttemptId, 'original-attempt');
  assert.equal(results[3][0][2].studySnapshot.exercise_id, 'exercise-1');
  assert.equal(results[3][0][3].defenseCandidateId, 'candidate-1');
  assert.deepEqual(results[3][0][0].openingDecisionManifest, fixtures.available.cards[0].opening_decision_manifest);
  assert.deepEqual(results[3][0].map(card => card.revision), [2, 2, 2, 2, 2, 2]);
  console.log('PASS actual_worker_malformed_request_and_producer_result_decoding');
} finally { await browser.close(); }
