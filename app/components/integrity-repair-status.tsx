import { useEffect, useRef, useState } from "react";
import { Button } from "./buttons/BaseButton";
import { usesLocalApi } from "../utils/local";
import { discardStaleIntegrityRepair, flushIntegrityRepairs, INTEGRITY_REPAIRS_CHANGED,
  INTEGRITY_REPAIR_CONFIRMED, pendingIntegrityRepairs, retryIntegrityRepair,
  type PendingIntegrityRepair } from "../lib/integrity-repair-outbox";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";

export function IntegrityRepairStatus({ onConfirmed, onResume }: {
  onConfirmed: (repertoireId: string) => void; onResume: (repertoireId: string) => void;
}) {
  const [repairs, setRepairs] = useState<PendingIntegrityRepair[]>([]);
  const [error, setError] = useState("");
  const statusContainer = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ onConfirmed, onResume });
  useEffect(() => { callbacks.current = { onConfirmed, onResume }; }, [onConfirmed, onResume]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let releaseFrame = 0;
    const releaseLayout = () => {
      if (!statusContainer.current?.style.minHeight) return;
      cancelAnimationFrame(releaseFrame);
      // Chessground must resolve this drop before the board changes location.
      releaseFrame = requestAnimationFrame(() => statusContainer.current?.style.removeProperty("min-height"));
    };
    const update = () => {
      if (statusContainer.current && document.querySelector(".board-viewport piece.dragging"))
        statusContainer.current.style.minHeight = `${statusContainer.current.getBoundingClientRect().height}px`;
      try {
        const next = pendingIntegrityRepairs(); setRepairs(next); setError("");
        for (const repair of next) {
          if (!["failed", "blocked", "stale"].includes(repair.phase)) continue;
          const key = `integrity-repair:${repair.operationId}`;
          const existing = notifications().find(record => record.key === key && !record.resolvedAt);
          if (!existing || existing.message !== repair.error) publishNotification({ key, severity: "error",
            source: "repertoire repair", message: repair.error ?? "Repair needs attention." });
        }
      } catch (failure) { setError(failure instanceof Error ? failure.message : "Saved repairs could not be read."); }
    };
    const flush = () => { if (navigator.onLine) void flushIntegrityRepairs().catch(failure =>
      setError(failure instanceof Error ? failure.message : "Repair recovery could not run.")); };
    const confirmed = (event: Event) => {
      const { repertoireId } = (event as CustomEvent<{ repertoireId: string }>).detail;
      // Completion is passive: it updates counts without focusing/opening a dialog.
      for (const notification of notifications().filter(record => record.source === "repertoire repair" && !record.resolvedAt)) {
        if (!pendingIntegrityRepairs().some(repair => `integrity-repair:${repair.operationId}` === notification.key))
          resolveNotification(notification.id, { severity: "success", message: "Repertoire repair confirmed." });
      }
      callbacks.current.onConfirmed(repertoireId);
    };
    update(); flush();
    window.addEventListener(INTEGRITY_REPAIRS_CHANGED, update);
    window.addEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed);
    window.addEventListener("storage", update);
    window.addEventListener("online", flush);
    document.addEventListener("visibilitychange", flush);
    document.addEventListener("mouseup", releaseLayout);
    document.addEventListener("touchend", releaseLayout);
    document.addEventListener("pointercancel", releaseLayout);
    window.addEventListener("blur", releaseLayout);
    const interval = window.setInterval(flush, 3_000);
    return () => {
      cancelAnimationFrame(releaseFrame);
      document.removeEventListener("mouseup", releaseLayout);
      document.removeEventListener("touchend", releaseLayout);
      document.removeEventListener("pointercancel", releaseLayout);
      window.removeEventListener("blur", releaseLayout);
      window.clearInterval(interval); window.removeEventListener(INTEGRITY_REPAIRS_CHANGED, update);
      window.removeEventListener(INTEGRITY_REPAIR_CONFIRMED, confirmed); window.removeEventListener("storage", update);
      window.removeEventListener("online", flush); document.removeEventListener("visibilitychange", flush);
    };
  }, []);
  return <div ref={statusContainer} data-integrity-repair-status style={{ display: "flow-root" }}>{error && <div className="ui-notice error" role="alert">{error}</div>}
    {repairs.map(repair => <div className="integrity-train-notice" role={repair.error ? "alert" : "status"} key={repair.operationId}>
      <strong>{repair.phase === "queued" ? "Repair queued on this device" : repair.phase === "saving" ? "Repair saving" :
        repair.phase === "validating" ? "Repair validating" : "Repair needs attention"}</strong>
      <span>{repair.error ?? (repair.phase === "queued" ? "Waiting to send · you can keep studying." : "Working in the background · you can keep studying.")}</span>
      {repair.phase === "stale" ? <Button onClick={() => {
        try { discardStaleIntegrityRepair(repair.operationId); callbacks.current.onResume(repair.repertoireId); }
        catch (failure) { setError(String(failure)); }
      }}>Refresh repair</Button> : (repair.error || ["failed", "blocked"].includes(repair.phase)) &&
        <Button onClick={() => void retryIntegrityRepair(repair.operationId).catch(failure => setError(String(failure)))}>Retry repair</Button>}
    </div>)}
  </div>;
}
