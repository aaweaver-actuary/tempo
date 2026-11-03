import { build } from 'esbuild';
import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const built = await build({ entryPoints: ['app/lib/study.worker.ts'], bundle: true, platform: 'browser', format: 'iife', write: false });
const optional = await build({ entryPoints: ['app/lib/optional-study.worker.ts'], bundle: true, platform: 'browser', format: 'iife', write: false });
const decoder = await build({ entryPoints: ['app/lib/study-result-decoder.ts'], bundle: true, platform: 'browser', format: 'iife', globalName: 'studyDecoder', write: false });
const fixtures = JSON.parse(await readFile('tests/fixtures/queue-wire/producer.json', 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  await page.route('http://localhost/**', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html>' }));
  await page.goto('http://localhost/');
  await page.addScriptTag({ content: decoder.outputFiles[0].text });
  const results = await page.evaluate(async ({ source, optionalSource, fixtures }) => {
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }));
    const optionalUrl = URL.createObjectURL(new Blob([optionalSource], { type: 'text/javascript' }));
    const send = (request, entry = url) => new Promise(resolve => {
      const worker = new Worker(entry);
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
      const fen = fixtures.available.cards[0].start_fen;
      const lines = ['d2d4', 'e2e4'].map((move, index) => ({ id: `line-${index}`, repertoireId: 'rep',
        repertoireName: 'Rep', title: `Line ${index}`, side: 'white', startingFen: fen, moves: [move] }));
      const deck = { kind: 'deck', deckId: 'deck', records: [2, 1].map(DeckPosition => ({ DeckId: 'deck',
        DeckPosition, PuzzleId: `puzzle-${DeckPosition}`, FEN: fen, Moves: 'e2e4 e7e5', Rating: 1000 })) };
      const metadata = { kind: 'workspace', url: '/api/progress', payload: { states: {}, activity: [],
        reviewedToday: 0, cleanCards: 0, dueToday: 0, totalCards: 0 } };
      const snapshot = { kind: 'repertoireAlternatives', generation: 3, lines };
      const lineTask = { kind: 'lines', lines };
      const extra = [await send({ id: 9, task: deck }), await send({ id: 10, task: metadata }),
        await send({ id: 11, task: snapshot }, optionalUrl), await send({ id: 12, task: lineTask }, optionalUrl)];
      const quarantinedTask = { kind: 'queue', payload: { cards: [prepared.cards[0],
        { ...prepared.cards[0], id: 'malformed-worker-card', start_fen: 'invalid-fen' }], count: 2 } };
      const quarantinedReply = await send({ id: 13, task: quarantinedTask });
      const quarantined = { id: quarantinedReply.id,
        cards: studyDecoder.decodeStudyResult(quarantinedTask, quarantinedReply.result),
        diagnostics: quarantinedReply.diagnostics };
      extra[2].mapIsValid = extra[2].result.movesByRepertoire instanceof Map &&
        [...extra[2].result.movesByRepertoire.values()].every(value => value instanceof Map &&
          [...value.values()].every(moves => moves instanceof Set));
      return [await send({ id: 3, task: { kind: 'bad' } }), await send({ id: 'bad', task: {} }),
        await send({ id: 4, task: { kind: 'queue', payload: { cards: [], count: 0 } } }), decoded, extra, quarantined];
    }
    finally { URL.revokeObjectURL(url); URL.revokeObjectURL(optionalUrl); }
  }, { source: built.outputFiles[0].text, optionalSource: optional.outputFiles[0].text, fixtures });
  assert.deepEqual(results[0], { id: 3, error: 'Invalid study worker request' });
  assert.ok(results[1].workerError);
  assert.equal(results[2].id, 4); assert.deepEqual(results[2].result, []);
  assert.deepEqual(results[3].map(cards => cards.map(card => card.kind)), [['opening', 'puzzle', 'study', 'defense', 'endgame', 'middlegame'], ['opening', 'puzzle', 'study', 'defense', 'endgame', 'middlegame'], []]);
  assert.equal(results[3][0][0].openingEvidenceParentAttemptId, 'original-attempt');
  assert.equal(results[3][0][2].studySnapshot.exercise_id, 'exercise-1');
  assert.equal(results[3][0][3].defenseCandidateId, 'candidate-1');
  assert.deepEqual(results[3][0][0].openingDecisionManifest, fixtures.available.cards[0].opening_decision_manifest);
  assert.deepEqual(results[3][0].map(card => card.revision), [2, 2, 2, 2, 2, 2]);
  assert.deepEqual(results[4][0].result.map(item => item.record.DeckPosition), [1, 2]);
  assert.equal(results[4][1].result.totalCards, 0);
  assert.equal(results[4][2].result.generation, 3);
  assert.equal(results[4][2].mapIsValid, true);
  assert.deepEqual(results[4][3].result.map(line => line.id), ['line-0', 'line-1']);
  assert.equal(results[5].id, 13);
  assert.equal(results[5].cards.length, 1);
  assert.equal(results[5].cards[0].backendId, fixtures.available.cards[0].id);
  assert.ok(results[5].diagnostics.some(issue => issue.recordId === 'malformed-worker-card'));
  console.log('PASS actual_worker_malformed_request_and_producer_result_decoding');
} finally { await browser.close(); }
