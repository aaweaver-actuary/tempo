import { useEffect, useRef, useState } from "react";
import { flushPendingReviews, pendingReviews, ReviewReplayError, type ReviewFlushResult } from "../lib/review-outbox";
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
      let review;
      try { review = pendingReviews()[0]; }
      catch (error) { reportDebugError(error, { source: "training-review-storage", operation: "read saved reviews" }); return; }
      if (!review) return;
      const identity = review.attemptId ?? String(review.queueEntryId);
      if (deadlines.current.get(identity)?.retryAt === Infinity) return;
      const delay = Math.max(1000, (deadlines.current.get(identity)?.retryAt ?? 0) - Date.now());
      timer = setTimeout(() => {
        const recover = () => {
          idleId = undefined;
          if (disposed || navigator.onLine === false || document.visibilityState === "hidden") return;
          running.current = true;
          void flushPendingReviews(1, true).then(result => {
            deadlines.current.delete(identity);
            // The receipt is authoritative even if foreground readiness changed during the read.
            deferredConfirmations.current.push(result);
          }).catch(error => {
            const previous = deadlines.current.get(identity)?.failures ?? 0;
            const retryable = error instanceof ReviewReplayError && ["pending", "transient"].includes(error.classification) &&
              !(error.cause instanceof PendingOperationError && error.cause.blocked);
            deadlines.current.set(identity, { failures: previous + 1,
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
