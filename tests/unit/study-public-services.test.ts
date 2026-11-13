import { expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReviewIntent, ReviewServices, ReviewConfirmation } from '../../app/features/study/contracts';

/** A second consumer needs semantic facts, not an IndexedDB or state-store implementation. */
async function consume(services: ReviewServices, intent: ReviewIntent) {
  const authority = await services.authority.capture();
  const id = await services.reviews.retain(intent, authority);
  const pending = services.reviews.pending();
  const result = await services.reviews.flush(authority, { stopAfterAttemptId: id });
  return { id, pending, result, current: authority.isCurrent() };
}

it('a fake semantic consumer retains original identity and observes pending, committed and conflicted facts', async () => {
  const intent: ReviewIntent = { backendId: 'card', queueEntryId: 17, attemptId: 'original', outcome: 'again', guided: true, completedAt: 'original-clock', expectedRevision: 3 };
  const facts: ReviewIntent[] = []; let current = true;
  const confirmation: ReviewConfirmation = { persistedAttemptIds: ['original'], conflictedAttemptIds: ['other'] };
  const services: ReviewServices = {
    reviews: { retain: async (value, authority) => { if (!authority.isCurrent()) throw new Error('authority changed'); facts.push(value); return value.attemptId!; }, pending: () => facts,
      recoverable: () => facts, conflicted: () => [{ ...intent, attemptId: 'other', state: 'conflicted', conflict: { code: 'stale', message: 'changed', retryable: false } }],
      logicalAttemptId: value => value.attemptId!, flush: async authority => { if (!authority.isCurrent()) throw new Error('authority changed'); return confirmation; }, recover: async (_id, authority) => { if (!authority.isCurrent()) throw new Error('authority changed'); return confirmation; } },
    authority: { capture: async () => ({ authority: 'original-authority', isCurrent: () => current }) },
    progression: { currentAttempt: () => ({ attemptId: 'original', generation: 2 }), currentCard: () => { throw new Error('not used'); },
      setReviewSaveError: () => {}, setPendingReviewError: () => {}, advancePrepared: () => false,
      startReview: async () => {}, retryNext: async () => {}, invalidateWorkspace: () => {} },
    diagnostics: { classify: () => ({ kind: 'other', message: '', blocked: false }), reportSaveFailure: () => {}, reportReplayFailure: () => {},
      publish: () => 'notice', update: () => {}, resolve: () => {}, find: () => undefined, beginTiming: () => () => {} },
  };
  const result = await consume(services, intent);
  expect(result).toEqual({ id: 'original', pending: [intent], result: confirmation, current: true });
  expect(services.reviews.conflicted()[0]).toMatchObject({ attemptId: 'other', state: 'conflicted', conflict: { code: 'stale' } });
  const lease = await services.authority.capture(); current = false; expect(lease.isCurrent()).toBe(false);
  await expect(services.reviews.retain(intent, lease)).rejects.toThrow('authority changed');
  await expect(services.reviews.flush(lease)).rejects.toThrow('authority changed');
});

it('browser mapping retains the supplied identity and invalidates an old authority lease', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory()); localStorage.clear();
  const storage = await import('../../database/browser/study-intent-storage');
  try {
    await storage.initializeStudyIntentStorage();
    const { createBrowserReviewServices } = await import('../../app/features/study/browser-services');
    const services = createBrowserReviewServices(); const lease = await services.authority.capture();
    const { PendingOperationError } = await import('../../app/lib/operation-status');
    expect(services.diagnostics.classify(new PendingOperationError('original-operation'))).toMatchObject({ kind: 'pending', operationId: 'original-operation', blocked: false });
    const intent: ReviewIntent = { backendId: 'card', queueEntryId: 17, attemptId: 'original', outcome: 'again', guided: true, completedAt: '2026-11-01T12:00:00Z', expectedRevision: 3 };
    expect(await services.reviews.retain(intent, lease)).toBe('original');
    expect(services.reviews.pending()).toEqual([intent]);
    const stored = await (await storage.studyIntentStore()).get('review:original');
    expect(stored).toMatchObject({ attemptId: 'original', requestId: 'review-attempt:original', payload: intent, state: 'pending' });
    expect(lease.isCurrent()).toBe(true);
    const { defaultStudyClient } = await import('../../app/lib/study-connection'); defaultStudyClient.disconnectStudy();
    expect(lease.isCurrent()).toBe(false);
    await expect(services.progression.startReview('original', lease)).rejects.toThrow('authority changed');
    const replacement = await services.authority.capture(); storage.closeStudyIntentStorage(); expect(replacement.isCurrent()).toBe(false);
  } finally { storage.closeStudyIntentStorage(); vi.unstubAllGlobals(); vi.resetModules(); }
});

it.each(['retain', 'flush'] as const)('browser semantic %s refuses an authority switch during owner preflight', async action => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory()); localStorage.clear();
  const storage = await import('../../database/browser/study-intent-storage');
  try {
    const store = await storage.initializeStudyIntentStorage(); const { createBrowserReviewServices } = await import('../../app/features/study/browser-services');
    const services = createBrowserReviewServices(); const lease = await services.authority.capture();
    const intent: ReviewIntent = { backendId: 'card', queueEntryId: 17, attemptId: 'original', outcome: 'again', guided: false, completedAt: '2026-11-01T12:00:00Z', expectedRevision: 3 };
    if (action === 'flush') await services.reviews.retain(intent, lease);
    let started!: () => void, release!: () => void;
    const waiting = new Promise<void>(done => { started = done; }); const gate = new Promise<void>(done => { release = done; });
    const original = store.get.bind(store); let first = true;
    const spy = vi.spyOn(store, 'get').mockImplementation(async key => { if (key === 'review:original' && first) { first = false; started(); await gate; } return original(key); });
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    const pending = action === 'retain' ? services.reviews.retain(intent, lease) : services.reviews.flush(lease, { stopAfterAttemptId: 'original' });
    await waiting; const { defaultStudyClient } = await import('../../app/lib/study-connection'); defaultStudyClient.disconnectStudy(); release();
    await expect(pending).rejects.toThrow('authority changed'); spy.mockRestore();
    if (action === 'retain') expect(await store.get('review:original')).toBeUndefined();
    else expect(await store.get('review:original')).toMatchObject({ state: 'pending', attemptId: 'original', requestId: 'review-attempt:original', payload: intent });
    expect(fetcher).not.toHaveBeenCalled();
  } finally { storage.closeStudyIntentStorage(); vi.unstubAllGlobals(); vi.resetModules(); }
});

it('browser semantic flush preserves the original review if authority changes during delivery', async () => {
  vi.resetModules(); vi.stubGlobal('indexedDB', new IDBFactory()); localStorage.clear();
  const storage = await import('../../database/browser/study-intent-storage');
  try {
    const store = await storage.initializeStudyIntentStorage(); const { createBrowserReviewServices } = await import('../../app/features/study/browser-services');
    const services = createBrowserReviewServices(); const lease = await services.authority.capture();
    const intent: ReviewIntent = { backendId: 'card', queueEntryId: 17, attemptId: 'original', outcome: 'correct', guided: false, completedAt: '2026-11-01T12:00:00Z', expectedRevision: 3 };
    await services.reviews.retain(intent, lease);
    let release!: (response: Response) => void, entered!: () => void; const waiting = new Promise<void>(done => { entered = done; });
    const fetcher = vi.fn(() => { entered(); return new Promise<Response>(done => { release = done; }); }); vi.stubGlobal('fetch', fetcher);
    const pending = services.reviews.flush(lease, { stopAfterAttemptId: 'original' }); await waiting;
    const { defaultStudyClient } = await import('../../app/lib/study-connection'); defaultStudyClient.disconnectStudy(); release(Response.json({ persisted: true }));
    await expect(pending).rejects.toThrow('authority changed');
    expect(await store.get('review:original')).toMatchObject({ state: 'pending', attemptId: 'original', requestId: 'review-attempt:original', payload: intent });
    expect(fetcher).toHaveBeenCalledOnce();
  } finally { storage.closeStudyIntentStorage(); vi.unstubAllGlobals(); vi.resetModules(); }
});
