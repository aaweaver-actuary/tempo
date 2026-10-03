import { useEffect, useRef, useState } from "react";
import { recoverOpeningEvidence, subscribeOpeningEvidenceLeaseRelease } from "../lib/opening-evidence-journal";
import { publishNotification } from "../lib/notifications";
import { useTrainingStore } from "../state/training-store";

/** Yield until the queue is usable and the browser has an idle opportunity. */
export function useOpeningEvidenceRecovery(enabled: boolean, ready: boolean, blocked: boolean): void {
  const pending = useRef(true);
  const running = useRef(false);
  const mounted = useRef(false);
  const [recoveryGeneration, setRecoveryGeneration] = useState(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!enabled) return;
    const requestRecovery = () => {
      pending.current = true;
      setRecoveryGeneration(generation => generation + 1);
    };
    window.addEventListener("online", requestRecovery);
    const unsubscribeLeaseRelease = subscribeOpeningEvidenceLeaseRelease(requestRecovery);
    return () => { window.removeEventListener("online", requestRecovery); unsubscribeLeaseRelease(); };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || !ready || blocked || !pending.current || running.current) return;
    let canceled = false;
    const recover = () => {
      if (canceled || useTrainingStore.getState().queueReadiness !== "ready") return;
      pending.current = false;
      running.current = true;
      void recoverOpeningEvidence().then(result => {
        if (result.moreWork) pending.current = true;
        running.current = false;
        // A new render schedules a new idle callback and rechecks foreground state.
        if (mounted.current && pending.current) setRecoveryGeneration(generation => generation + 1);
      }).catch(error => {
        running.current = false;
        pending.current = true; // Retry at the next readiness/connectivity opportunity, never spin.
        publishNotification({
          severity: "warning", source: "opening evidence", key: "opening-evidence-recovery",
          message: `Opening evidence recovery is pending. Normal training continues. ${String(error)}`,
        });
      });
    };
    if (typeof window.requestIdleCallback === "function") {
      const idleId = window.requestIdleCallback(recover);
      return () => { canceled = true; window.cancelIdleCallback(idleId); };
    }
    // A task after the first paint is the fallback; no elapsed-time readiness guess.
    const channel = new MessageChannel();
    channel.port1.onmessage = recover;
    const frameId = requestAnimationFrame(() => channel.port2.postMessage(null));
    return () => {
      canceled = true;
      cancelAnimationFrame(frameId);
      channel.port1.close(); channel.port2.close();
    };
  }, [enabled, ready, blocked, recoveryGeneration]);
}
