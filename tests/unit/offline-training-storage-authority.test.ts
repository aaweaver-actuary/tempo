import { expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';

it('FE25 keys offline handles by locator and never moves retained A/B records', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory());
  try {
    const { offlineTrainingDatabase } = await import('../../database/browser/offline-training-storage');
    const { readPreparedTraining, updatePreparedTraining } = await import('../../database/browser/prepared-training-storage');
    let generation = 1;
    const a = { locator: 'fe25-A', requireCurrent: () => { if (generation !== 1) throw new Error('authority changed'); } };
    const initial = { localDate: '2026-11-01', preparedAt: 'A', cards: [], attempts: [], nextTemporaryId: 1 };
    await updatePreparedTraining(() => initial, a); const first = await offlineTrainingDatabase(a);
    generation = 2;
    const b = { locator: 'fe25-B', requireCurrent: () => { if (generation !== 2) throw new Error('authority changed'); } };
    const second = await offlineTrainingDatabase(b); expect(second).not.toBe(first); expect([first.name, second.name]).toEqual(['fe25-A', 'fe25-B']);
    await expect(readPreparedTraining(a)).rejects.toThrow('authority changed'); expect(await readPreparedTraining(b)).toBeNull();
    const dataB = { ...initial, preparedAt: 'B' }; await updatePreparedTraining(() => dataB, b);
    generation = 1; expect(await readPreparedTraining(a)).toEqual(initial);
    generation = 2; expect(await readPreparedTraining(b)).toEqual(dataB);
    first.close(); second.close();
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});

it('FE25 same-locator generation change aborts stale prepared transaction', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory());
  try {
    const { offlineTrainingDatabase } = await import('../../database/browser/offline-training-storage');
    const { readPreparedTraining, updatePreparedTraining } = await import('../../database/browser/prepared-training-storage');
    let generation = 1; const lease = (expected: number) => ({ locator: 'fe25-same', requireCurrent: () => { if (generation !== expected) throw new Error('authority changed'); } });
    const old = lease(1), initial = { localDate: '2026-11-01', preparedAt: 'original', cards: [], attempts: [], nextTemporaryId: 1 };
    await updatePreparedTraining(() => initial, old); const first = await offlineTrainingDatabase(old);
    await expect(updatePreparedTraining(value => { generation = 2; return { ...value!, preparedAt: 'stale' }; }, old)).rejects.toThrow('authority changed');
    const next = lease(2); expect(await offlineTrainingDatabase(next)).toBe(first);
    expect(await readPreparedTraining(next)).toEqual(initial); first.close();
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});

it('FE25 delayed offline open cannot migrate an old authority to a new one', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory());
  try {
    const { offlineTrainingDatabase } = await import('../../database/browser/offline-training-storage');
    let generation = 1;
    const old = { locator: 'fe25-delayed-A', requireCurrent: () => { if (generation !== 1) throw new Error('authority changed'); } };
    const opened = offlineTrainingDatabase(old); generation = 2;
    await expect(opened).rejects.toThrow('authority changed');
    const next = { locator: 'fe25-delayed-B', requireCurrent: () => { if (generation !== 2) throw new Error('authority changed'); } };
    const databaseB = await offlineTrainingDatabase(next); expect(databaseB.name).toBe('fe25-delayed-B');
    generation = 1; const databaseA = await offlineTrainingDatabase(old); expect(databaseA.name).toBe('fe25-delayed-A');
    expect(databaseA).not.toBe(databaseB); databaseA.close(); databaseB.close();
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});

it('FE25 journal append aborts an uncommitted stale callback without changing either locator', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory());
  try {
    const { offlineTrainingDatabase } = await import('../../database/browser/offline-training-storage');
    const { appendOpeningAttempt } = await import('../../database/browser/opening-evidence-journal-storage');
    const { evidenceCompletion } = await import('../fixtures/opening-evidence-storage');
    let generation = 1, checks = 0;
    const a = { locator: 'fe25-journal-A', requireCurrent: () => {
      if (generation !== 1 || ++checks >= 2) throw new Error('authority changed');
    } };
    const openA = { locator: a.locator, requireCurrent: () => {} };
    const b = { locator: 'fe25-journal-B', requireCurrent: () => {} };
    const dbA = await offlineTrainingDatabase(openA), dbB = await offlineTrainingDatabase(b);
    const checkpoint = evidenceCompletion('original');
    const { events, ...header } = checkpoint;
    const attempt = { ...header, owner_session_id: 'owner', final_sequence: events.length, delivery_state: 'pending' as const };
    await expect(appendOpeningAttempt(dbA, attempt, events, checkpoint, a)).rejects.toThrow('authority changed');
    const read = (db: IDBDatabase) => new Promise<unknown>((resolve, reject) => {
      const request = db.transaction('opening_attempts').objectStore('opening_attempts').get('original');
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
    });
    expect(await read(dbA)).toBeUndefined(); expect(await read(dbB)).toBeUndefined();
    generation = 2; dbA.close(); dbB.close();
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});

it.each(['other owner', 'changed header', 'changed event', 'newer sequence', 'missing anchor'])('FE25 rebind holds a %s without overwriting the original evidence', async conflict => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory());
  try {
    const { offlineTrainingDatabase } = await import('../../database/browser/offline-training-storage');
    const { appendOpeningAttempt } = await import('../../database/browser/opening-evidence-journal-storage');
    const { evidenceCompletion } = await import('../fixtures/opening-evidence-storage');
    const binding = { locator: `fe25-conflict-${conflict}`, requireCurrent: () => {} };
    const db = await offlineTrainingDatabase(binding); const checkpoint = evidenceCompletion('retained-original');
    const { events, ...header } = checkpoint;
    const attempt = { ...header, owner_session_id: 'original-owner', final_sequence: events.length, delivery_state: 'pending' as const };
    const existing = { ...attempt, ...(conflict === 'other owner' ? { owner_session_id: 'foreign' } : {}),
      ...(conflict === 'changed header' ? { queue_entry_id: 202 } : {}),
      ...(conflict === 'newer sequence' ? { final_sequence: events.length + 1 } : {}) };
    const event = { ...events[0], ...(conflict === 'changed event' ? { response_uci: 'a2a3' } : {}), attempt_id: checkpoint.attempt_id };
    await new Promise<void>((resolve, reject) => { const tx = db.transaction(['opening_attempts','opening_events'], 'readwrite');
      if (conflict !== 'missing anchor') { tx.objectStore('opening_attempts').put(existing); tx.objectStore('opening_events').put(event); }
      else tx.objectStore('opening_attempts').get('missing');
      tx.oncomplete = () => resolve(); tx.onerror = () => reject(tx.error); });
    await expect(appendOpeningAttempt(db, attempt, events, checkpoint, binding, true)).rejects.toThrow('remains held');
    const read = (store: string, key: IDBValidKey) => new Promise<unknown>((resolve, reject) => { const request = db.transaction(store).objectStore(store).get(key);
      request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); });
    expect(await read('opening_attempts', checkpoint.attempt_id)).toEqual(conflict === 'missing anchor' ? undefined : existing);
    expect(await read('opening_events', [checkpoint.attempt_id, 1])).toEqual(conflict === 'missing anchor' ? undefined : event); db.close();
  } finally { vi.unstubAllGlobals(); vi.resetModules(); }
});
