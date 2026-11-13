import { build } from 'esbuild';
import { expect, type BrowserContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { openingDecisionManifestSchema, type OpeningEvidenceCheckpoint } from '../../app/domain/opening-evidence';

// Routed shell/bootstrap/checkpoint traffic is controlled; IDB, Web Locks,
// generation changes and the bundled production owners run in the browser.
const manifest = openingDecisionManifestSchema.parse(JSON.parse(readFileSync(new URL('../fixtures/opening-evidence-manifest.json', import.meta.url), 'utf8')));
const credential = 'browser-fixture-credential-not-secret';
const workspace = 'fe25-browser-workspace';
const bootstrap = { schema_version: 1, workspace_id: workspace, timezone: 'UTC', local_date: new Date().toISOString().slice(0, 10),
  settings: { study_only_mode: false, new_cards_per_day: 1, study_new_per_day: 1, tactics_new_per_day: 1,
    include_defensive_cards_in_daily_stack: false, light_first_interval_days: 1 } };
const queueCard = { id: manifest.card_id, revision: manifest.card_revision, queue_entry_id: 101,
  start_fen: manifest.decisions[0].fen, moves: ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'],
  content_type: 'opening', repertoire_name: 'FE25 owned opening', repertoire_source: 'PGN',
  trained_color: 'white', opening_decision_manifest: manifest };

type Api = typeof import('../../app/lib/opening-evidence-journal') & typeof import('../../app/lib/study-connection') & {
  useTrainingStore: typeof import('../../app/state/training-store').useTrainingStore;
  mapQueueCardToPracticeCard: typeof import('../../app/domain/adapters/practice-card-adapters').mapQueueCardToPracticeCard;
  enqueuePendingReview: typeof import('../../app/lib/review-outbox').enqueuePendingReview;
  recordOfflineAttempt: typeof import('../../app/lib/offline-training').recordOfflineAttempt;
  studyIntentStore: typeof import('../../database/browser/study-intent-storage').studyIntentStore;
  createBrowserReviewServices: typeof import('../../app/features/study/browser-services').createBrowserReviewServices;
};
type Capture = import('../../app/lib/opening-evidence-journal').OpeningAttemptJournal;
type BrowserState = { fe25Proof: Api; fe25Capture?: Capture; fe25Fault: 'none' | 'abort' | 'rotate' | 'expose'; fe25Aborts: number; fe25Pending?: Promise<string> };

export async function createFe25Harness(page: Page, context: BrowserContext, noLocks = false) {
  const bundle = await build({ stdin: { contents: `
    export * from './app/lib/opening-evidence-journal';
    export * from './app/lib/study-connection';
    export { useTrainingStore } from './app/state/training-store';
    export { mapQueueCardToPracticeCard } from './app/domain/adapters/practice-card-adapters';
    export { enqueuePendingReview } from './app/lib/review-outbox';
    export { recordOfflineAttempt } from './app/lib/offline-training';
    export { studyIntentStore } from './database/browser/study-intent-storage';
    export { createBrowserReviewServices } from './app/features/study/browser-services';
  `, resolveDir: process.cwd() }, bundle: true, format: 'iife', globalName: 'fe25Proof', write: false,
    platform: 'browser', define: { __TEMPO_STUDY__: 'true', __TEMPO_API_URL__: '""' }, loader: { '.svg': 'dataurl', '.css': 'empty' } });
  const requests: { url: string; body: string | null; key: string | undefined }[] = [];
  await context.route('**/fe25-evidence-harness', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>FE25 test harness</title>' }));
  await context.route('**/api/study/bootstrap', route => route.fulfill({ json: { ...bootstrap, workspace_id: route.request().headers()['x-tempo-workspace'] ?? workspace } }));
  await context.route('**/api/opening-evidence/checkpoints', async route => {
    requests.push({ url: route.request().url(), body: route.request().postData(), key: route.request().headers()['idempotency-key'] });
    await route.abort('failed');
  });
  await context.route('**/api/operations/**', route => route.fulfill({ status: 404, json: {} }));
  await page.addInitScript(({ disableLocks }) => {
    const state = window as unknown as BrowserState;
    state.fe25Fault = 'none'; state.fe25Aborts = 0;
    if (disableLocks) Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (value: unknown, key?: IDBValidKey) {
      const request = key === undefined ? put.call(this, value) : put.call(this, value, key);
      if (this.name === 'opening_events' && state.fe25Fault === 'abort') {
        state.fe25Aborts++; this.transaction.abort();
      } else if (this.name === 'opening_events' && state.fe25Fault === 'rotate') {
        state.fe25Fault = 'none';
        request.addEventListener('success', () => state.fe25Proof.disconnectStudy(), { once: true });
      } else if (this.name === 'opening_events' && state.fe25Fault === 'expose') {
        state.fe25Fault = 'none';
        const completion = state.fe25Capture!.snapshot();
        state.fe25Pending = state.fe25Proof.enqueuePendingReview({ backendId: completion.manifest.card_id,
          queueEntryId: completion.queue_entry_id!, attemptId: completion.attempt_id, outcome: 'correct', guided: false,
          expectedRevision: completion.manifest.card_revision, openingEvidenceCompletion: completion });
      }
      return request;
    };
  }, { disableLocks: noLocks });
  await page.goto('/fe25-evidence-harness');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.evaluate(async ({ credential, workspace }) => {
    const api = (window as unknown as BrowserState).fe25Proof;
    await api.connectStudy(location.origin, workspace, credential);
  }, { credential, workspace });
  const observer = await context.newPage();
  await observer.goto('/fe25-evidence-harness');
  async function connectObserverOwner() {
    await observer.addScriptTag({ content: bundle.outputFiles[0].text });
    await observer.evaluate(async ({ credential, workspace }) => {
      await (window as unknown as BrowserState).fe25Proof.connectStudy(location.origin, workspace, credential);
    }, { credential, workspace });
  }
  const databaseName = await page.evaluate(() => (window as unknown as BrowserState).fe25Proof.studyOfflineDatabaseName());
  async function createAbortedCapture() {
    await page.evaluate((raw) => {
      const state = window as unknown as BrowserState, api = state.fe25Proof;
      const card = api.mapQueueCardToPracticeCard(raw);
      api.useTrainingStore.getState().initializeCardState(card);
      const attempt = api.useTrainingStore.getState().attempt;
      if (!attempt.attemptId || !attempt.freshEvidenceAttempt) throw new Error('ordinary owner did not create fresh attempt');
      const capture = api.beginOpeningAttempt(card, attempt.attemptId, { freshAttempt: attempt.freshEvidenceAttempt });
      if (!capture) throw new Error('capture not created');
      state.fe25Capture = capture; state.fe25Fault = 'abort';
      capture.response(raw.opening_decision_manifest.decisions[0].move_offset,
        raw.opening_decision_manifest.decisions[0].expected_uci, 'expected');
    }, queueCard);
    await expect.poll(() => page.evaluate(() => (window as unknown as BrowserState).fe25Aborts)).toBeGreaterThan(0);
    await expect.poll(() => page.evaluate(() => Boolean((window as unknown as BrowserState).fe25Capture?.storageError))).toBe(true);
    return page.evaluate(() => (window as unknown as BrowserState).fe25Capture!.snapshot());
  }
  const reconnect = () => page.evaluate(async ({ credential, workspace }) => {
    await (window as unknown as BrowserState).fe25Proof.connectStudy(location.origin, workspace, credential);
  }, { credential, workspace });
  const recover = () => page.evaluate(async () => {
    try { await (window as unknown as BrowserState).fe25Proof.recoverOpeningEvidence(); } catch { /* State below is the evidence. */ }
  });
  const rows = (attemptId: string) => observer.evaluate(({ name, attemptId }) => new Promise<{ attempt: unknown; events: unknown[]; training: unknown }>((resolve, reject) => {
    const opened = indexedDB.open(name); opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const db = opened.result; const tx = db.transaction(['opening_attempts', 'opening_events', 'training']);
      const attempt = tx.objectStore('opening_attempts').get(attemptId);
      const events = tx.objectStore('opening_events').index('attempt_id').getAll(attemptId);
      const training = tx.objectStore('training').get('prepared-daily-queue');
      tx.oncomplete = () => { db.close(); resolve({ attempt: attempt.result, events: events.result, training: training.result }); };
      tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }), { name: databaseName, attemptId });
  return { page, observer, databaseName, requests, createAbortedCapture, reconnect, recover, rows, connectObserverOwner };
}

export type Fe25Harness = Awaited<ReturnType<typeof createFe25Harness>>;
export async function setFault(page: Page, value: BrowserState['fe25Fault']) {
  await page.evaluate(fault => { (window as unknown as BrowserState).fe25Fault = fault; }, value);
}
export async function retainOnline(page: Page, completion: OpeningEvidenceCheckpoint) {
  await page.evaluate(async completion => {
    const api = (window as unknown as BrowserState).fe25Proof;
    await api.enqueuePendingReview({ backendId: completion.manifest.card_id, queueEntryId: completion.queue_entry_id!,
      attemptId: completion.attempt_id, outcome: 'correct', guided: false, expectedRevision: completion.manifest.card_revision,
      openingEvidenceCompletion: completion });
  }, completion);
}
