import { expect, it, vi } from 'vitest';
import { IDBFactory } from 'fake-indexeddb';
import type { ReviewIntent, ReviewServices, ReviewConfirmation } from '../../app/features/study/contracts';

/** A second consumer needs semantic facts, not an IndexedDB or state-store implementation. */
async function consume(services: ReviewServices, intent: ReviewIntent) {
  const authority = await services.authority.capture();
  const id = await services.reviews.retain(intent);
  const pending = services.reviews.pending();
  const result = await services.reviews.flush({ stopAfterAttemptId: id });
  return { id, pending, result, current: authority.isCurrent() };
}

it('a fake semantic consumer retains original identity and observes pending, committed and conflicted facts', async () => {
  const intent: ReviewIntent = { backendId: 'card', queueEntryId: 17, attemptId: 'original', outcome: 'again', guided: true, completedAt: 'original-clock', expectedRevision: 3 };
  const facts: ReviewIntent[] = []; let current = true;
  const confirmation: ReviewConfirmation = { persistedAttemptIds: ['original'], conflictedAttemptIds: ['other'] };
  const services: ReviewServices = {
    reviews: { retain: async value => { facts.push(value); return value.attemptId!; }, pending: () => facts,
      recoverable: () => facts, conflicted: () => [{ ...intent, attemptId: 'other', state: 'conflicted', conflict: { code: 'stale', message: 'changed', retryable: false } }],
      logicalAttemptId: value => value.attemptId!, flush: async () => confirmation, recover: async () => confirmation },
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
    expect(await services.reviews.retain(intent)).toBe('original');
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
