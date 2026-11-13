/** Browser wiring for semantic review ports; the controller still owns presentation. */
import { defaultStudyClient, studyWorkspace } from '../../lib/study-connection';
import { studyIntentStore, isStudyIntentStoreCurrent } from '../../../database/browser/study-intent-storage';
import { enqueuePendingReview, flushPendingReviews, pendingReviews, recoverableReviews, conflictedReviews, logicalAttemptId, reviewStudySession, ReviewReplayError } from '../../lib/review-outbox';
import { PendingOperationError } from '../../lib/operation-status';
import { advancePreparedContinuation } from '../../lib/prepared-review-continuation';
import { useTrainingStore } from '../../state/training-store';
import { invalidateWorkspaceData } from '../../lib/workspace-data';
import { reportReviewSaveFailure } from '../../lib/review-save-notice';
import { reportDebugError } from '../../lib/debug-reporting';
import { notifications, publishNotification, resolveNotification, updateNotification } from '../../lib/notifications';
import { measureTempoOperation, measureTempoDragPhase } from '../../lib/performance';
import type { ReviewServices, ReviewFailure, ReviewAuthority } from './contracts';

export function createBrowserReviewServices(): ReviewServices {
  const classify = (error: unknown): ReviewFailure => {
    if (error instanceof ReviewReplayError) return { kind: 'review', message: error.message, blocked: error.blocked,
      classification: error.classification, identity: { backendId: error.backendId, queueEntryId: error.queueEntryId, attemptId: error.attemptId },
      endpoint: error.endpoint, status: error.status, code: error.code, retryable: error.retryable, confirmedBeforeFailure: error.flushResult };
    if (error instanceof PendingOperationError) return { kind: 'pending', message: error.message, blocked: error.blocked, operationId: error.operationId };
    return { kind: 'other', message: error instanceof Error ? error.message : String(error), blocked: false };
  };
  const bindings = new WeakMap<ReviewAuthority, { store: Awaited<ReturnType<typeof studyIntentStore>>; requireCurrent: () => void; offline: { locator: string; requireCurrent: () => void } }>();
  const ensure = (authority: ReviewAuthority) => {
    if (!bindings.has(authority) || !authority.isCurrent()) throw new Error('Study authority changed. Recover the original review with its original service.');
  };
  const bindingFor = (authority: ReviewAuthority) => {
    const binding = bindings.get(authority);
    if (!binding) throw new Error('Study authority binding is unavailable.');
    binding.requireCurrent(); return binding;
  };
  return {
    reviews: { retain: (intent, authority) => enqueuePendingReview(intent, bindingFor(authority)), pending: pendingReviews, recoverable: recoverableReviews,
      conflicted: conflictedReviews, logicalAttemptId,
      flush: (authority, options) => flushPendingReviews(options?.maximum, options?.verifyReceiptFirst, options?.explicitRetryAttemptId, options?.deferredAttemptIds, options?.stopAfterAttemptId, bindingFor(authority)),
      recover: (attemptId, authority) => flushPendingReviews(Infinity, true, attemptId, undefined, undefined, bindingFor(authority)) },
    authority: { async capture() {
      const authority = studyWorkspace(), origin = defaultStudyClient.origin, generation = defaultStudyClient.generation, locator = defaultStudyClient.studyOfflineDatabaseName();
      const store = await studyIntentStore();
      const isCurrent = () => { try { return studyWorkspace() === authority && defaultStudyClient.origin === origin &&
        defaultStudyClient.generation === generation && isStudyIntentStoreCurrent(store); } catch { return false; } };
      if (!isCurrent()) throw new Error('Study authority changed. Recover the original review with its original service.');
      const lease = Object.freeze({ authority, isCurrent });
      const requireCurrent = () => { if (!isCurrent()) throw new Error('Study authority changed. Recover the original review with its original service.'); if (defaultStudyClient.studyOfflineDatabaseName() !== locator) throw new Error('Study offline storage locator changed.'); };
      bindings.set(lease, { store, requireCurrent, offline: { locator, requireCurrent } });
      return lease;
    } },
    progression: { currentAttempt: () => { const { attemptId, generation } = useTrainingStore.getState().attempt; return { attemptId, generation }; },
      currentCard: () => useTrainingStore.getState().getCard(),
      setReviewSaveError: message => useTrainingStore.getState().setReviewSaveError(message),
      setPendingReviewError: message => useTrainingStore.getState().setPendingReviewError(message),
      advancePrepared: advancePreparedContinuation,
      startReview: async (attemptId, authority) => { ensure(authority); const session = await reviewStudySession(); ensure(authority); session.start(`review:${attemptId}`); },
      retryNext: async (attemptId, authority, acquire, stillCurrent, retryable) => {
        ensure(authority); const session = await reviewStudySession(); ensure(authority);
        return session.retryNext(`review:${attemptId}`, async (signal, acquisition) => { ensure(authority); const result = await acquire(signal, acquisition); ensure(authority); return result; },
          () => authority.isCurrent() && stillCurrent(), retryable);
      },
      invalidateWorkspace: invalidateWorkspaceData },
    diagnostics: { classify, reportSaveFailure: reportReviewSaveFailure,
      reportReplayFailure(error) { if (!(error instanceof ReviewReplayError)) return;
        reportDebugError(error, { kind: 'api', source: 'training-review-replay', operation: 'save pending review', endpoint: error.endpoint,
          status: error.status, retryable: error.retryable, cardId: error.backendId, queueEntryId: error.queueEntryId,
          attemptId: error.attemptId, code: error.code, classification: error.classification, notify: false }); },
      publish: publishNotification, update: updateNotification, resolve: resolveNotification,
      find: id => notifications().find(record => record.id === id),
      beginTiming: kind => kind === 'queue-reconciliation' ? measureTempoOperation(kind) : measureTempoDragPhase(kind),
    },
  };
}
