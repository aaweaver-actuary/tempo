import { useEffect, useRef, useState } from "react";
import { flushPendingReviews, logicalAttemptId, recoverableReviews, ReviewReplayError, type ReviewFlushResult } from "../lib/review-outbox";
import { useCommittedCallback } from "./use-committed-callback";
import { PendingOperationError } from "../lib/operation-status";
import { subscribeOperationStatusChange } from "../lib/operation-status-events";
import { reportDebugError } from "../lib/debug-reporting";

/** One retained review per idle opportunity. Manual saves share the outbox's in-flight guard. */
export function usePendingReviewRecovery(enabled: boolean, ready: boolean, blocked: boolean,
  onConfirmed: (result: ReviewFlushResult) => void): void {
  const deadlines = useRef(new Map<string, { failures: number; retryAt: number }>());
  const confirmed = useCommittedCallback(onConfirmed);
  const pointerHeld = useRef(false);
  const running = useRef(false);
  const deferredConfirmations = useRef<ReviewFlushResult[]>([]);
  const [confirmationGeneration, setConfirmationGeneration] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let idleId: number | undefined;
    const schedule = () => {
      clearTimeout(timer);
      if (idleId !== undefined) { window.cancelIdleCallback(idleId); idleId = undefined; }
      if (disposed || running.current || pointerHeld.current || !ready || blocked || navigator.onLine === false || document.visibilityState === "hidden") return;
      for (const result of deferredConfirmations.current.splice(0)) confirmed(result);
      const deferredAttemptIds = new Set([...deadlines.current]
        .filter(([, deadline]) => deadline.retryAt > Date.now()).map(([attemptId]) => attemptId));
      let review;
      let retainedCandidates;
      try {
        retainedCandidates = recoverableReviews();
        review = recoverableReviews(undefined, deferredAttemptIds)[0];
      }
      catch (error) { reportDebugError(error, { source: "training-review-storage", operation: "read saved reviews" }); return; }
      if (!review) {
        // With no independent work, reserve the earliest finite retry opportunity.
        review = retainedCandidates.filter((candidate, index) => Number.isFinite(deadlines.current.get(logicalAttemptId(candidate))?.retryAt) &&
          !retainedCandidates.slice(0, index).some(earlier => earlier.backendId === candidate.backendId))
          .sort((left, right) => deadlines.current.get(logicalAttemptId(left))!.retryAt - deadlines.current.get(logicalAttemptId(right))!.retryAt)[0];
        if (!review) return;
        deferredAttemptIds.delete(logicalAttemptId(review));
      }
      const identity = logicalAttemptId(review);
      const delay = Math.max(1000, (deadlines.current.get(identity)?.retryAt ?? 0) - Date.now());
      timer = setTimeout(() => {
        const recover = () => {
          idleId = undefined;
          if (disposed || navigator.onLine === false || document.visibilityState === "hidden") return;
          running.current = true;
          void flushPendingReviews(1, true, undefined, deferredAttemptIds).then(result => {
            for (const confirmedIdentity of [...result.persistedAttemptIds, ...result.conflictedAttemptIds])
              deadlines.current.delete(confirmedIdentity);
            // The receipt is authoritative even if foreground readiness changed during the read.
            deferredConfirmations.current.push(result);
          }).catch(error => {
            if (error instanceof ReviewReplayError && error.flushResult &&
                error.flushResult.persistedAttemptIds.length + error.flushResult.conflictedAttemptIds.length) {
              for (const settledIdentity of [...error.flushResult.persistedAttemptIds, ...error.flushResult.conflictedAttemptIds])
                deadlines.current.delete(settledIdentity);
              deferredConfirmations.current.push(error.flushResult);
            }
            // A manual flush may be shared with this idle observer. Attribute its
            // result to the attempted review, never the independent scheduled one.
            const failedIdentity = error instanceof ReviewReplayError ? error.attemptId : identity;
            const previous = deadlines.current.get(failedIdentity)?.failures ?? 0;
            const retryable = error instanceof ReviewReplayError && ["pending", "transient"].includes(error.classification) &&
              !(error.cause instanceof PendingOperationError && error.cause.blocked);
            // The outbox durably suppresses terminal/blocked attempts before throwing.
            // Keep no second terminal latch that could survive an explicit retry.
            if (error instanceof ReviewReplayError && (error.blocked || ["failed", "conflict"].includes(error.classification)))
              deadlines.current.delete(failedIdentity);
            else deadlines.current.set(failedIdentity, { failures: previous + 1,
              retryAt: retryable ? Date.now() + Math.min(30000, 1000 * 2 ** previous) : Infinity });
            reportDebugError(error, { source: "training-review-replay", operation: "recover saved review", notify: false });
          }).finally(() => {
            running.current = false;
            setConfirmationGeneration(generation => generation + 1);
            schedule();
          });
        };
        if (typeof window.requestIdleCallback === "function") idleId = window.requestIdleCallback(recover);
        else recover();
      }, delay);
    };
    const changed = () => {
      // A terminal error is retried only by an explicit user action, not an outbox projection.
      schedule();
    };
    const pointerStarted = () => { pointerHeld.current = true; schedule(); };
    const pointerEnded = () => {
      pointerHeld.current = false;
      schedule();
    };
    window.addEventListener("pointerdown", pointerStarted, true);
    window.addEventListener("pointerup", pointerEnded, true);
    window.addEventListener("pointercancel", pointerEnded, true);
    window.addEventListener("tempo:review-outbox", changed);
    window.addEventListener("online", schedule);
    document.addEventListener("visibilitychange", schedule);
    const unsubscribe = subscribeOperationStatusChange(operationId => {
      if (running.current) return; // Receipt reads announce status too; they must not erase their own backoff.
      for (const identity of deadlines.current.keys()) if (operationId === `review-attempt:${identity}` || operationId.startsWith(`review-reconcile:${identity}:`))
        deadlines.current.delete(identity);
      schedule();
    });
    schedule();
    return () => { disposed = true; clearTimeout(timer); unsubscribe();
      if (idleId !== undefined) window.cancelIdleCallback(idleId);
      window.removeEventListener("pointerdown", pointerStarted, true);
      window.removeEventListener("pointerup", pointerEnded, true);
      window.removeEventListener("pointercancel", pointerEnded, true);
      window.removeEventListener("tempo:review-outbox", changed);
      window.removeEventListener("online", schedule);
      document.removeEventListener("visibilitychange", schedule); };
  }, [enabled, ready, blocked, confirmed, confirmationGeneration]);
}
