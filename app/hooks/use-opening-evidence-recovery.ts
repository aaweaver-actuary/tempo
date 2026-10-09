import { useEffect, useRef, useState } from "react";
import { readOpeningEvidenceRecoverySchedule, recoverOpeningEvidence, subscribeOpeningEvidenceAppend, subscribeOpeningEvidenceLeaseRelease, subscribeOpeningEvidenceOperationResume } from "../lib/opening-evidence-journal";
import { openingEvidenceRecoveryPolicy } from "../lib/opening-evidence-recovery-policy";
import { publishNotification } from "../lib/notifications";
import { useTrainingStore } from "../state/training-store";

/** Retry eligibility is separate from queue/foreground/idle admission. */
export function useOpeningEvidenceRecovery(enabled: boolean, ready: boolean, blocked: boolean): void {
  const pending = useRef(true);
  const running = useRef(false);
  const active = useRef(false);
  const lifecycle = useRef(0);
  const suspended = useRef(false);
  const retryAt = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [recoveryGeneration, setRecoveryGeneration] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    active.current = true; lifecycle.current++;
    pending.current = true;
    const requestRecovery = () => {
      pending.current = true;
      setRecoveryGeneration(generation => generation + 1);
    };
    // Re-enable the wake-up timer; journal deadlines remain authoritative across events.
    if (retryAt.current > Date.now()) retryTimer.current = setTimeout(() => {
      retryTimer.current = undefined; retryAt.current = 0; requestRecovery();
    }, retryAt.current - Date.now());
    window.addEventListener("online", requestRecovery);
    window.addEventListener("offline", requestRecovery);
    const unsubscribeAppend = subscribeOpeningEvidenceAppend(requestRecovery);
    const unsubscribeLeaseRelease = subscribeOpeningEvidenceLeaseRelease(requestRecovery);
    const unsubscribeOperationResume = subscribeOpeningEvidenceOperationResume(requestRecovery);
    return () => {
      active.current = false; lifecycle.current++;
      clearTimeout(retryTimer.current); retryTimer.current = undefined;
      window.removeEventListener("online", requestRecovery); window.removeEventListener("offline", requestRecovery);
      unsubscribeAppend(); unsubscribeLeaseRelease(); unsubscribeOperationResume();
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !ready || blocked || !pending.current || running.current || suspended.current ||
      navigator.onLine === false) return;
    let canceled = false;
    const recover = () => {
      if (canceled || navigator.onLine === false || useTrainingStore.getState().queueReadiness !== "ready") return;
      pending.current = false; running.current = true;
      const startedLifecycle = lifecycle.current;
      const requestNextSlice = () => setRecoveryGeneration(generation => generation + 1);
      const isCurrent = () => active.current && startedLifecycle === lifecycle.current;
      const reportRecoveryFailure = (error: unknown) => {
        const policy = openingEvidenceRecoveryPolicy(error);
        try {
          publishNotification({
            severity: policy === "retry" ? "info" : "warning", source: "opening evidence", key: "opening-evidence-recovery",
            details: { error: String(error), recoveryPolicy: policy },
            message: policy === "retry" ? "Opening details are waiting to sync. You can keep training."
              : policy === "blocked" ? "Opening details could not sync. Resolve and retry the blocked operation in Jobs. You can keep training."
              : "Opening details could not be read or stored. Keep this browser's data, restore storage access, then reopen Tempo.",
          });
        } catch { /* Diagnostics cannot change retry or suspension policy. */ }
        return policy;
      };
      void recoverOpeningEvidence().catch(async error => {
        if (!isCurrent()) return;
        if (reportRecoveryFailure(error) === "suspend") { suspended.current = true; return; }
        try { return await readOpeningEvidenceRecoverySchedule(); }
        catch (scheduleError) { suspended.current = true; reportRecoveryFailure(scheduleError); }
      }).then(result => {
        running.current = false;
        if (!isCurrent()) { if (active.current) { pending.current = true; requestNextSlice(); } return; }
        if (!result) return;
        clearTimeout(retryTimer.current); retryTimer.current = undefined;
        retryAt.current = result.nextRetryAt ?? 0;
        if (result.nextRetryAt !== undefined) retryTimer.current = setTimeout(() => {
          retryTimer.current = undefined; retryAt.current = 0;
          if (active.current) { pending.current = true; requestNextSlice(); } // Never run journal work from the timer.
        }, Math.max(0, result.nextRetryAt - Date.now()));
        if (result.moreWork) pending.current = true;
        // Ready journals yield to another idle slice without inheriting another journal's delay.
        if (pending.current) requestNextSlice();
      });
    };
    if (typeof window.requestIdleCallback === "function") {
      const idleId = window.requestIdleCallback(recover);
      return () => { canceled = true; window.cancelIdleCallback(idleId); };
    }
    const channel = new MessageChannel();
    channel.port1.onmessage = () => { channel.port1.close(); channel.port2.close(); recover(); };
    const frameId = requestAnimationFrame(() => channel.port2.postMessage(null));
    return () => {
      canceled = true; cancelAnimationFrame(frameId);
      channel.port1.close(); channel.port2.close();
    };
  }, [enabled, ready, blocked, recoveryGeneration]);
}
