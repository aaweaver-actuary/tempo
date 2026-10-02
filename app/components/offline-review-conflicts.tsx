import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "./buttons/BaseButton";
import { discardOfflineConflict, readPreparedTraining, type OfflineAttempt, type PreparedTraining } from "../lib/offline-training";
import { notifications, publishNotification, resolveNotification, subscribeNotifications } from "../lib/notifications";
import { isIPhoneHomeScreen } from "../utils/local";

import { useDialogFocus } from "../hooks/use-dialog-focus";

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
  const [open, setOpen] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    void readPreparedTraining().then((saved) => {
      if (active) setPrepared(saved);
    }).catch((failure) => {
      if (active) setError(`Could not read saved conflicts: ${String(failure)}`);
    });
    return () => { active = false; };
  }, [conflictNoticeUpdatedAt]);

  const attempts = conflictAttempts(prepared);
  if (!attempts.length && !error) return null;
  const conflictData = JSON.stringify({ schemaVersion: 1, exportedAt: new Date().toISOString(), attempts }, null, 2);

  async function discard(localEntryId: number) {
    try {
      const saved = await discardOfflineConflict(localEntryId);
      setPrepared(saved);
      setError("");
      const remaining = conflictAttempts(saved);
      updateConflictNotice(remaining);
      if (!remaining.length) setOpen(false);
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

  return <div className="offline-conflicts-control">
    <Button type="button" className="offline-conflicts-trigger" onClick={event => { event.currentTarget.focus(); setOpen(true); }}>
      Review conflicts{attempts.length ? ` (${attempts.length})` : ""}
    </Button>
    {open && <div className="offline-conflicts-backdrop" onClick={() => setOpen(false)}>
      <ConflictDialog onClose={() => setOpen(false)}>
        <header><h2>Review conflicts</h2><Button type="button" onClick={() => setOpen(false)}>Close</Button></header>
        <p>The computer’s saved reviews take priority. These phone attempts remain on this device until you discard each one.</p>
        {error && <p role="alert">{error}</p>}
        <Button type="button" onClick={() => void copy()}>{copied ? "Copied conflict data" : "Copy conflict data"}</Button>
        {copyFailed && <p>Clipboard access is unavailable. Select and copy the data below.</p>}
        <textarea aria-label="Conflict data" readOnly value={conflictData}
          onFocus={(event) => event.currentTarget.select()} />
        <div className="offline-conflicts-list">
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
