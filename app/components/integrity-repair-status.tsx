import { useEffect, useRef, useState } from "react";
import { Button } from "./buttons/BaseButton";
import { usesLocalApi } from "../utils/local";
import { discardStaleIntegrityRepair, flushIntegrityRepairs, pendingIntegrityRepairs, retryIntegrityRepair, subscribeIntegrityRepairs,
  INTEGRITY_REPAIR_CONFIRMED, type PendingIntegrityRepair } from "../lib/integrity-repair-outbox";
import { publishNotification, notifications, resolveNotification } from "../lib/notifications";
import { updateBrowserActivity } from "../lib/browser-activity";

export function IntegrityRepairStatus({ onReconcile, onResume }: {
  onReconcile: (repertoireId: string) => void; onResume: (repertoireId: string) => void;
}) {
  const [repairs, setRepairs] = useState<PendingIntegrityRepair[]>([]);
  const [error, setError] = useState("");
  const callbacks = useRef({ onReconcile, onResume });
  useEffect(() => { callbacks.current = { onReconcile, onResume }; }, [onReconcile, onResume]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let timer: number | undefined;
    const observed = new Map<string, PendingIntegrityRepair>();
    const update = (externalRemoval = false) => {
      if (stopped) return;
      try {
        const next = pendingIntegrityRepairs(); setRepairs(next); setError("");
        for (const repair of next) {
          const needsAttention = ["failed", "blocked", "stale"].includes(repair.phase);
          const key = `integrity-repair:${repair.operationId}`;
          observed.set(key, repair);
          updateBrowserActivity(key, "Repertoire repair", needsAttention ? "failed" : repair.phase === "queued" ? "queued" : "running",
            repair.phase, repair.error);
          const unresolved = notifications().find(record => record.key === key && !record.resolvedAt);
          if (repair.error && (!unresolved || unresolved.message !== repair.error)) publishNotification({ key,
            severity: needsAttention ? "error" : "warning", source: "repertoire repair", message: repair.error });
          else if (!repair.error) {
            const previous = notifications().find(record => record.key === key && !record.resolvedAt);
            if (previous) resolveNotification(previous.id, { severity: "info", message: "Repair recovery is continuing." });
          }
        }
        const removedRepertoireIds = new Set<string>();
        for (const [key, repair] of observed) if (!next.some(repair => `integrity-repair:${repair.operationId}` === key)) {
          observed.delete(key);
          if (externalRemoval) removedRepertoireIds.add(repair.repertoireId);
          updateBrowserActivity(key, "Saved repair choice removed", "complete", "Removed from this device");
          const previous = notifications().find(record => record.key === key && !record.resolvedAt);
          if (previous) resolveNotification(previous.id, { severity: "info", message: "This saved repair no longer needs attention." });
        }
        for (const repertoireId of removedRepertoireIds) callbacks.current.onReconcile(repertoireId);
      } catch (failure) { setError(failure instanceof Error ? failure.message : "Saved repairs could not be read."); }
    };
    const flush = async () => {
      window.clearTimeout(timer);
      try { if (navigator.onLine) await flushIntegrityRepairs(); }
      catch (failure) { if (!stopped) setError(failure instanceof Error ? failure.message : "Repair recovery could not run."); }
      finally { if (!stopped) timer = window.setTimeout(() => void flush(), 3_000); }
    };
    const wake = () => { void flush(); };
    const confirmed = (event: Event) => {
      update();
      const { repertoireId, operationId } = (event as CustomEvent<{ repertoireId: string; operationId?: string }>).detail;
      if (operationId) updateBrowserActivity(`integrity-repair:${operationId}`, "Repertoire repair confirmed", "complete", "Validated");
      callbacks.current.onReconcile(repertoireId);
    };
    const storageChanged = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith("tempo-pending-integrity-repair")) update(true);
    };
    update(); wake();
    // Observe external removals before the shared subscription updates the observed journal.
    // A missing record requires fresh evidence; only the confirmation event reports validation.
    window.addEventListener("storage", storageChanged);
    const unsubscribe = subscribeIntegrityRepairs(() => update());
    window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
    window.addEventListener("online", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      stopped = true; window.clearTimeout(timer);
      unsubscribe();
      window.removeEventListener("storage", storageChanged);
      window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
      window.removeEventListener("online", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
  return <>{error && <p className="ui-notice error" role="alert">{error}</p>}
    {repairs.length > 0 && <div className="integrity-train-notice integrity-repair-status" role="status">
      <strong>{repairs.length} repair choice{repairs.length === 1 ? "" : "s"} awaiting confirmation</strong>
      <span>You can keep studying while these choices save.</span>
      {repairs.map(repair => <div key={repair.operationId}>
        <span>{repair.selectedMoveUci} · {repair.phase === "queued" ? "Waiting to send" : repair.phase === "saving" ? "Saving" :
          repair.phase === "validating" ? "Validating" : "Needs attention"}{repair.error ? ` · ${repair.error}` : ""}</span>
        {repair.phase === "stale" ? <>
          <Button onClick={() => callbacks.current.onResume(repair.repertoireId)}>Review changed conflict</Button>
          <Button onClick={() => { try { discardStaleIntegrityRepair(repair.operationId); } catch (failure) { setError(String(failure)); } }}>Discard obsolete choice</Button>
        </> : (repair.error || ["failed", "blocked"].includes(repair.phase)) &&
          <Button onClick={() => void retryIntegrityRepair(repair.operationId).catch(failure => setError(String(failure)))}>Retry repair</Button>}
      </div>)}
    </div>}
  </>;
}
