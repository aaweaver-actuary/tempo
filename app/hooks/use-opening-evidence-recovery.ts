import { useEffect, useRef, useState } from "react";
import { recoverOpeningEvidence, subscribeOpeningEvidenceLeaseRelease, subscribeOpeningEvidenceOperationResume } from "../lib/opening-evidence-journal";
import { openingEvidenceRecoveryPolicy, openingEvidenceRetryDelays } from "../lib/opening-evidence-recovery-policy";
import { publishNotification } from "../lib/notifications";
import { useTrainingStore } from "../state/training-store";

/** Retry eligibility is separate from queue/foreground/idle admission. */
export function useOpeningEvidenceRecovery(enabled: boolean, ready: boolean, blocked: boolean): void {
  const pending = useRef(true);
  const running = useRef(false);
  const active = useRef(false);
  const lifecycle = useRef(0);
  const suspended = useRef(false);
  const failures = useRef(0);
  const retryAt = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [recoveryGeneration, setRecoveryGeneration] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    active.current = true; lifecycle.current++;
    const requestRecovery = () => {
      pending.current = true;
      setRecoveryGeneration(generation => generation + 1);
    };
    // Re-enabling preserves an outstanding delay; events never shorten it.
    if (retryAt.current > Date.now()) retryTimer.current = setTimeout(() => {
      retryTimer.current = undefined; retryAt.current = 0; requestRecovery();
    }, retryAt.current - Date.now());
    window.addEventListener("online", requestRecovery);
    window.addEventListener("offline", requestRecovery);
    const unsubscribeLeaseRelease = subscribeOpeningEvidenceLeaseRelease(requestRecovery);
    const unsubscribeOperationResume = subscribeOpeningEvidenceOperationResume(requestRecovery);
    return () => {
      active.current = false; lifecycle.current++;
      clearTimeout(retryTimer.current); retryTimer.current = undefined;
      window.removeEventListener("online", requestRecovery); window.removeEventListener("offline", requestRecovery);
      unsubscribeLeaseRelease(); unsubscribeOperationResume();
    };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !ready || blocked || !pending.current || running.current || suspended.current ||
      retryAt.current > Date.now() || navigator.onLine === false) return;
    let canceled = false;
    const recover = () => {
      if (canceled || navigator.onLine === false || useTrainingStore.getState().queueReadiness !== "ready") return;
      pending.current = false; running.current = true;
      const startedLifecycle = lifecycle.current;
      const requestNextSlice = () => setRecoveryGeneration(generation => generation + 1);
      const isCurrent = () => active.current && startedLifecycle === lifecycle.current;
      void recoverOpeningEvidence().then(result => {
        running.current = false;
        if (!isCurrent()) { if (active.current) requestNextSlice(); return; }
        failures.current = 0; retryAt.current = 0;
        if (result.moreWork) pending.current = true;
        // Healthy backlog yields without inheriting error delay.
        if (pending.current) requestNextSlice();
      }).catch(error => {
        running.current = false;
        if (!isCurrent()) { if (active.current) requestNextSlice(); return; }
        const policy = openingEvidenceRecoveryPolicy(error);
        pending.current = true;
        if (policy === "retry") {
          const delay = openingEvidenceRetryDelays[Math.min(failures.current++, openingEvidenceRetryDelays.length - 1)];
          retryAt.current = Date.now() + delay;
          retryTimer.current = setTimeout(() => {
            retryTimer.current = undefined; retryAt.current = 0;
            if (active.current) requestNextSlice(); // Never run journal work from the timer.
          }, delay);
        } else if (policy === "blocked") {
          requestNextSlice(); // The journal defers that identity; other journals stay eligible.
        } else suspended.current = true;
        try {
          publishNotification({
            severity: "warning", source: "opening evidence", key: "opening-evidence-recovery",
            message: `Opening evidence recovery is pending. Normal training continues. ${String(error)} ${
              policy === "suspend" ? "Recovery is paused. Restore browser storage/access or repair the saved data, then reload Tempo to resume."
                : policy === "blocked" ? "Resolve and explicitly retry this operation; recovery resumes after its status changes." : "Recovery will retry after a bounded delay."}`,
          });
        } catch { /* Diagnostics cannot change retry or suspension policy. */ }
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
