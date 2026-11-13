import { test, expect } from '@playwright/test';
import { createFe25Harness, retainOnline, setFault } from './fe25-first-append-fixture';
import type { OpeningEvidenceCheckpoint } from '../../app/domain/opening-evidence';

type Api = typeof import('../../app/lib/opening-evidence-journal') & typeof import('../../app/lib/study-connection') & {
  enqueuePendingReview: typeof import('../../app/lib/review-outbox').enqueuePendingReview;
  recordOfflineAttempt: typeof import('../../app/lib/offline-training').recordOfflineAttempt;
  studyIntentStore: typeof import('../../database/browser/study-intent-storage').studyIntentStore;
};
type State = { fe25Proof: Api; fe25Capture: import('../../app/lib/opening-evidence-journal').OpeningAttemptJournal; fe25Aborts: number; fe25Pending?: Promise<string> };

test('FE25 real two-tab first abort reconnect retains exact original evidence', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  expect(original.events).toHaveLength(1);
  expect((await h.rows(original.attempt_id)).attempt).toBeUndefined();
  const secondTabAcquired = await h.observer.evaluate(attemptId => navigator.locks.request(`tempo-opening-attempt:${attemptId}`,
    { ifAvailable: true }, lock => Boolean(lock)), original.attempt_id);
  expect(secondTabAcquired).toBe(false);
  await setFault(page, 'none'); await h.reconnect(); await h.recover();
  await expect.poll(async () => Boolean((await h.rows(original.attempt_id)).attempt)).toBe(true);
  const saved = await h.rows(original.attempt_id);
  expect(saved.attempt).toMatchObject({ attempt_id: original.attempt_id, manifest: original.manifest, final_sequence: 1 });
  expect(saved.events).toEqual(original.events.map(event => ({ ...event, attempt_id: original.attempt_id })));
  expect(h.requests.every(request => request.body === null || JSON.parse(request.body).attempt_id === original.attempt_id)).toBe(true);
});

test('FE25 real online aggregate exposure holds absent first journal and original intent', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  const completion = await page.evaluate(() => (window as unknown as State).fe25Capture.finish('complete'));
  await retainOnline(page, completion);
  await setFault(page, 'none'); await h.reconnect(); await h.recover();
  expect((await h.rows(original.attempt_id)).attempt).toBeUndefined();
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.rebindHeld)).toBe(true);
  const saved = await page.evaluate(async id => (window as unknown as State).fe25Proof.studyIntentStore().then(store => store.get(`review:${id}`)), original.attempt_id);
  expect(saved).toMatchObject({ attemptId: original.attempt_id, requestId: `review-attempt:${original.attempt_id}`, state: 'pending' });
  expect(JSON.stringify(saved)).toContain(original.attempt_id);
  expect(h.requests).toEqual([]);
});

test('FE25 real phone aggregate exposure preserves original offline review and holds evidence', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  const completion = await page.evaluate(() => (window as unknown as State).fe25Capture.finish('complete'));
  await h.observer.evaluate(({ name, completion }) => new Promise<void>((resolve, reject) => {
    const opened = indexedDB.open(name); opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const db = opened.result, tx = db.transaction('training', 'readwrite');
      tx.objectStore('training').put({ localDate: new Date().toISOString().slice(0, 10), preparedAt: completion.started_at,
        nextTemporaryId: 1000000, attempts: [], cards: [{ id: completion.manifest.card_id, queue_entry_id: 101,
          revision: completion.manifest.card_revision, cycle: 0, content_type: 'opening', moves: ['e2e4'],
          scheduling_mode: 'normal', recent_attempts_json: '[]' }] }, 'prepared-daily-queue');
      tx.oncomplete = () => { db.close(); resolve(); }; tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }), { name: h.databaseName, completion });
  await page.evaluate(async completion => {
    await (window as unknown as State).fe25Proof.recordOfflineAttempt(101, 'correct', false, undefined,
      { attemptId: completion.attempt_id, completion });
  }, completion);
  await setFault(page, 'none'); await h.reconnect(); await h.recover();
  const saved = await h.rows(original.attempt_id);
  expect(saved.attempt).toBeUndefined();
  expect((saved.training as { attempts: { attemptId: string; openingEvidenceCompletion: OpeningEvidenceCheckpoint }[] }).attempts)
    .toEqual([expect.objectContaining({ attemptId: original.attempt_id, openingEvidenceCompletion: completion })]);
  expect(h.requests).toEqual([]);
});

test('FE25 committed-but-unknown retry remains held after old-tab journal removal', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  await setFault(page, 'none'); await h.reconnect(); await setFault(page, 'rotate'); await h.recover();
  await expect.poll(async () => Boolean((await h.rows(original.attempt_id)).attempt)).toBe(true);
  // Simulate an older tab which did not participate in the new lock, after an
  // ambiguous acknowledgment. Delete only this test-owned journal identity.
  await h.observer.evaluate(({ name, attemptId }) => new Promise<void>((resolve, reject) => {
    const opened = indexedDB.open(name); opened.onerror = () => reject(opened.error);
    opened.onsuccess = () => {
      const db = opened.result, tx = db.transaction(['opening_attempts', 'opening_events'], 'readwrite');
      tx.objectStore('opening_attempts').delete(attemptId);
      const cursor = tx.objectStore('opening_events').index('attempt_id').openCursor(attemptId);
      cursor.onsuccess = () => { if (cursor.result) { cursor.result.delete(); cursor.result.continue(); } };
      tx.oncomplete = () => { db.close(); resolve(); }; tx.onabort = () => { db.close(); reject(tx.error); };
    };
  }), { name: h.databaseName, attemptId: original.attempt_id });
  await h.reconnect(); await h.recover();
  expect((await h.rows(original.attempt_id)).attempt).toBeUndefined();
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.rebindHeld)).toBe(true);
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.uncommittedEvents.size)).toBe(1);
});

test('FE25 ordinary review can be retained without Web Locks', async ({ page, context }) => {
  const h = await createFe25Harness(page, context, true);
  const original = await h.createAbortedCapture();
  await page.evaluate(async original => {
    const api = (window as unknown as State).fe25Proof;
    await api.enqueuePendingReview({ backendId: original.manifest.card_id, queueEntryId: original.queue_entry_id!,
      attemptId: original.attempt_id, outcome: 'correct', guided: false, expectedRevision: original.manifest.card_revision });
  }, original);
  const saved = await page.evaluate(async id => (window as unknown as State).fe25Proof.studyIntentStore().then(store => store.get(`review:${id}`)), original.attempt_id);
  expect(saved).toMatchObject({ attemptId: original.attempt_id, requestId: `review-attempt:${original.attempt_id}`, state: 'pending' });
  expect((await h.rows(original.attempt_id)).attempt).toBeUndefined();
});


test('FE25 real aggregate enqueue aborts an in-flight absent-anchor insertion', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  const completion = await page.evaluate(() => (window as unknown as State).fe25Capture.finish('complete'));
  await expect.poll(() => page.evaluate(() => (window as unknown as State).fe25Aborts)).toBeGreaterThan(1);
  await setFault(page, 'none'); await h.reconnect(); await setFault(page, 'expose'); await h.recover();
  await page.evaluate(async () => { await (window as unknown as State).fe25Pending; });
  const saved = await h.rows(original.attempt_id);
  expect(saved.attempt).toBeUndefined(); expect(saved.events).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.snapshot())).toEqual(completion);
  const retained = await page.evaluate(async id => (window as unknown as State).fe25Proof.studyIntentStore().then(store => store.get(`review:${id}`)), original.attempt_id);
  expect(retained).toMatchObject({ attemptId: original.attempt_id, requestId: `review-attempt:${original.attempt_id}`, state: 'pending' });
  expect(h.requests).toEqual([]);
});

test('FE25 foreign authority never inserts original first-abort capture', async ({ page, context }) => {
  const h = await createFe25Harness(page, context);
  const original = await h.createAbortedCapture();
  await setFault(page, 'none');
  await page.evaluate(async () => {
    await (window as unknown as State).fe25Proof.connectStudy(location.origin, 'fe25-foreign-workspace', 'browser-fixture-credential-not-secret');
  });
  await h.recover();
  expect((await h.rows(original.attempt_id)).attempt).toBeUndefined();
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.snapshot())).toEqual(original);
  expect(await page.evaluate(() => (window as unknown as State).fe25Capture.uncommittedEvents.size)).toBe(1);
  expect(h.requests).toEqual([]);
});
