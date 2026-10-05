import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "./buttons/BaseButton";
import { discardOfflineConflict, readPreparedTraining, type OfflineAttempt, type PreparedTraining } from "../lib/offline-training";
import { notifications, publishNotification, resolveNotification, subscribeNotifications } from "../lib/notifications";
import { isIPhoneHomeScreen } from "../utils/local";

import { useDialogFocus } from "../hooks/use-dialog-focus";
import { conflictedReviews, discardReviewConflict, flushPendingReviews, retryReviewConflict, type PendingReview } from "../lib/review-outbox";

function ConflictDialog({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  const dialogRef = useRef<HTMLElement>(null);
  useDialogFocus(dialogRef, onClose);
  return <section ref={dialogRef} role="dialog" tabIndex={-1} aria-modal="true" aria-label="Review conflicts" className="offline-conflicts-dialog" onClick={event => event.stopPropagation()}>{children}</section>;
}

function conflictAttempts(prepared: PreparedTraining | null): OfflineAttempt[] {
  return prepared?.attempts.filter((attempt) => Boolean(attempt.conflict)) ?? [];
}

function updateConflictNotice(attempts: OfflineAttempt[]) {
  if (attempts.length) {
    const phone = isIPhoneHomeScreen();
    publishNotification({ severity: "warning", source: "phone review sync", key: "phone-review-conflicts",
      message: `${attempts.length} ${phone ? "phone" : "offline"} review conflict(s) remain saved ${phone ? "on this phone" : "in this browser"}. The computer's saved results take priority.`,
      details: { cardIds: attempts.map((attempt) => attempt.cardId) },
    });
  } else {
    const prior = notifications().find((record) => record.key === "phone-review-conflicts" && !record.resolvedAt);
    if (prior) resolveNotification(prior.id, { severity: "success", message: "Phone review conflicts cleared." });
  }
}

export function OfflineReviewConflicts() {
  const records = useSyncExternalStore(subscribeNotifications, notifications);
  const conflictNoticeUpdatedAt = records.find((record) => record.key === "phone-review-conflicts")?.updatedAt;
  const [prepared, setPrepared] = useState<PreparedTraining | null>(null);
  const [onlineConflicts, setOnlineConflicts] = useState<PendingReview[]>([]);
  const [retrying, setRetrying] = useState<string>();
  const [open, setOpen] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void (typeof indexedDB === "undefined" ? Promise.resolve(null) : readPreparedTraining()).then((saved) => {
      if (active) setPrepared(saved);
    }).catch((failure) => {
      if (active) setError(`Could not read saved conflicts: ${String(failure)}`);
    });
    return () => { active = false; };
  }, [conflictNoticeUpdatedAt]);

  useEffect(() => {
    const load = () => {
      try { setOnlineConflicts(conflictedReviews()); }
      catch (failure) { setError(`Could not read saved conflicts: ${String(failure)}`); }
    };
    load();
    window.addEventListener("tempo:review-outbox", load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener("tempo:review-outbox", load);
      window.removeEventListener("storage", load);
    };
  }, []);

  const attempts = conflictAttempts(prepared);
  const conflictCount = attempts.length + onlineConflicts.length;
  if (!conflictCount && !error && !retrying) return null;
  const conflictData = JSON.stringify({ schemaVersion: 1, exportedAt: new Date().toISOString(), attempts, onlineReviews: onlineConflicts }, null, 2);

  async function retryOnline(attemptId: string) {
    setRetrying(attemptId);
    try {
      retryReviewConflict(attemptId);
      await flushPendingReviews();
      setError("");
    } catch (failure) {
      setError(`The result remains saved and will retry when connected. ${String(failure)}`);
    } finally {
      setOnlineConflicts(conflictedReviews());
      setRetrying(undefined);
    }
  }

  function discardOnline(attemptId: string) {
    try { discardReviewConflict(attemptId); setOnlineConflicts(conflictedReviews()); setError(""); }
    catch (failure) { setError(`Could not discard the saved attempt: ${String(failure)}`); }
  }

  async function discard(localEntryId: number) {
    try {
      const saved = await discardOfflineConflict(localEntryId);
      setPrepared(saved);
      setError("");
      const remaining = conflictAttempts(saved);
      updateConflictNotice(remaining);
      if (!remaining.length && !onlineConflicts.length) setOpen(false);
    } catch (failure) {
      setError(`Could not discard the saved attempt: ${String(failure)}`);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(conflictData);
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopyFailed(true);
      setCopied(false);
    }
  }

  return <div className={`offline-conflicts-control${open ? " is-open" : ""}`}>
    <Button type="button" className="offline-conflicts-trigger" onClick={event => { event.currentTarget.focus(); setOpen(true); }}>
      Review conflicts{conflictCount ? ` (${conflictCount})` : ""}
    </Button>
    {open && <div className="offline-conflicts-backdrop" onClick={() => setOpen(false)}>
      <ConflictDialog onClose={() => setOpen(false)}>
        <header><h2>Review conflicts</h2><Button type="button" onClick={() => setOpen(false)}>Close</Button></header>
        <p>These completed results remain saved on this device. Retry after resolving the conflict, or copy the data before discarding a result.</p>
        {error && <p role="alert">{error}</p>}
        <Button type="button" onClick={() => void copy()}>{copied ? "Copied conflict data" : "Copy conflict data"}</Button>
        {copyFailed && <p>Clipboard access is unavailable. Select and copy the data below.</p>}
        <textarea aria-label="Conflict data" readOnly value={conflictData}
          onFocus={(event) => event.currentTarget.select()} />
        <div className="offline-conflicts-list">
          {onlineConflicts.map((review) => <article key={review.attemptId} className="offline-conflict-item">
            <strong>Saved training result</strong>
            <dl>
              <div><dt>Time</dt><dd>{review.completedAt ? <time dateTime={review.completedAt}>{new Date(review.completedAt).toLocaleString()}</time> : "Original time unavailable"}</dd></div>
              <div><dt>Result</dt><dd>{review.outcome === "correct" ? "Correct" : "Again"}{review.guided ? " · Guided" : ""}</dd></div>
              <div><dt>Reason</dt><dd>{review.conflict?.message}</dd></div>
            </dl>
            <Button type="button" disabled={Boolean(retrying)} onClick={() => void retryOnline(review.attemptId!)}>Retry saved result</Button>
            <Button type="button" disabled={Boolean(retrying)} onClick={() => discardOnline(review.attemptId!)}>Discard saved result</Button>
          </article>)}
          {attempts.map((attempt) => {
            const card = prepared?.cards.find((candidate) => candidate.id === attempt.cardId);
            return <article key={attempt.localEntryId} className="offline-conflict-item">
              <strong>{card?.repertoire_name ?? "Saved card"}</strong>
              <dl>
                <div><dt>Card</dt><dd>{attempt.cardId}</dd></div>
                <div><dt>Time</dt><dd><time dateTime={attempt.completedAt}>{new Date(attempt.completedAt).toLocaleString()}</time></dd></div>
                <div><dt>Result</dt><dd>{attempt.outcome === "correct" ? "Correct" : "Again"}{attempt.guided ? " · Guided" : ""}</dd></div>
                <div><dt>Reason</dt><dd>{attempt.conflict}</dd></div>
              </dl>
              <Button type="button" onClick={() => void discard(attempt.localEntryId)}>Discard phone attempt</Button>
            </article>;
          })}
        </div>
      </ConflictDialog>
    </div>}
  </div>;
}
