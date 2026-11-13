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
