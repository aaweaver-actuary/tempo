"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { Button } from "./buttons/BaseButton";
import {
  buildNotificationExport, hideNotificationToast, hydrateNotifications,
  notificationToastIds, notifications,
  subscribeNotifications, NOTIFICATION_HISTORY_LIMIT,
  type NotificationRecord,
} from "../lib/notifications";
import { buildDebugBundle, copyDebugBundle, debugErrors } from "../lib/debug-reporting";

type Threshold = "info" | "warning" | "error";
const emptyNotificationRecords: readonly NotificationRecord[] = [];
const emptyToastIds: readonly string[] = [];

function useNotifications() {
  const records = useSyncExternalStore(subscribeNotifications, notifications, () => emptyNotificationRecords);
  useEffect(() => { hydrateNotifications(); }, []);
  return records;
}

function NotificationDetails({ record }: { record: NotificationRecord }) {
  if (!record.details || !Object.keys(record.details).length) return null;
  return <details className="notification-details">
    <summary>Details</summary>
    <dl>{Object.entries(record.details).map(([name, value]) => <div key={name}>
      <dt>{name}</dt><dd>{Array.isArray(value) ? value.join(", ") : String(value)}</dd>
    </div>)}</dl>
  </details>;
}

export function NotificationCenter() {
  const records = useNotifications();
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<"all" | NotificationRecord["severity"]>("all");
  const [threshold, setThreshold] = useState<Threshold>("info");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [debugCopyId, setDebugCopyId] = useState<string>();
  const [debugCopyState, setDebugCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const shown = filter === "all" ? records : records.filter((record) => record.severity === filter);
  const exportText = buildNotificationExport(threshold);
  const needsAttention = records.filter((record) => !record.resolvedAt &&
    (record.severity === "warning" || record.severity === "error")).length;

  async function copySelected() {
    try {
      await navigator.clipboard.writeText(exportText);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  return <div className="notification-center">
    <Button type="button" className="notification-trigger" aria-label="Notifications"
      aria-expanded={open} aria-controls="notification-tray" onClick={() => setOpen((value) => !value)}>
      <span aria-hidden="true">🔔</span><span className="notification-trigger-label">Notifications</span>
      {needsAttention > 0 && <span className="notification-count">{needsAttention}</span>}
    </Button>
    {open && <section id="notification-tray" className="notification-tray" aria-label="Notifications">
      <header><strong>Notifications</strong><Button type="button" onClick={() => setOpen(false)}>Close</Button></header>
      <p className="notification-retention">Newest first · latest {NOTIFICATION_HISTORY_LIMIT} kept on this device</p>
      <div className="notification-filters" aria-label="Filter notifications">
        {(["all", "error", "warning", "success", "info"] as const).map((severity) =>
          <Button type="button" key={severity} aria-pressed={filter === severity}
            onClick={() => setFilter(severity)}>{severity === "all" ? "All" : severity}</Button>)}
      </div>
      <div className="notification-export">
        <label htmlFor="notification-export-threshold">Copy severity</label>
        <select id="notification-export-threshold" value={threshold}
          onChange={(event) => { setThreshold(event.target.value as Threshold); setCopyState("idle"); }}>
          <option value="info">Info and higher</option>
          <option value="warning">Warning and higher</option>
          <option value="error">Error only</option>
        </select>
        <Button type="button" onClick={() => void copySelected()}>{copyState === "copied" ? "Copied JSON" : "Copy JSON"}</Button>
      </div>
      {copyState === "failed" && <div className="notification-copy-fallback">
        <p>Clipboard access is unavailable. Select and copy this JSON:</p>
        <textarea aria-label="Notification JSON" readOnly value={exportText} onFocus={(event) => event.currentTarget.select()} />
      </div>}
      <div className="notification-list">
        {shown.length === 0 && <p>No notifications in this filter.</p>}
        {shown.map((record) => <article key={record.id} className={`notification-item notification-${record.severity}`}>
          <div className="notification-item-meta"><span className="notification-severity">{record.severity}</span>
            <time dateTime={record.updatedAt}>{new Date(record.updatedAt).toLocaleString()}</time></div>
          <strong>{record.source}</strong><p>{record.message}</p><NotificationDetails record={record} />
          {typeof record.details?.debugRecordId === "string" &&
            debugErrors().some((debugRecord) => debugRecord.id === record.details?.debugRecordId) && <div className="notification-debug-actions">
            <Button type="button" onClick={() => {
              const debugRecordId = String(record.details?.debugRecordId);
              setDebugCopyId(debugRecordId);
              void copyDebugBundle(debugRecordId).then((copied) => setDebugCopyState(copied ? "copied" : "failed"));
            }}>{debugCopyId === record.details.debugRecordId && debugCopyState === "copied" ? "Copied debug info" : "Copy debug info"}</Button>
            <details><summary>Preview debug information</summary>
              <textarea aria-label="Debug information" readOnly value={buildDebugBundle(String(record.details.debugRecordId))} />
            </details>
            {debugCopyId === record.details.debugRecordId && debugCopyState === "failed" &&
              <p>Clipboard access is unavailable. Select the debug information above to copy it.</p>}
          </div>}
        </article>)}
      </div>
    </section>}
  </div>;
}

function NotificationToast({ record }: { record: NotificationRecord }) {
  const [exiting, setExiting] = useState(false);
  useEffect(() => {
    if (record.active) return;
    const duration = record.severity === "error" || record.severity === "warning" ? 7_000 : 4_000;
    const fadeTimer = window.setTimeout(() => setExiting(true), duration);
    const hideTimer = window.setTimeout(() => hideNotificationToast(record.id), duration + 250);
    return () => { window.clearTimeout(fadeTimer); window.clearTimeout(hideTimer); };
  }, [record.active, record.id, record.severity, record.updatedAt]);
  return <div className={`notification-toast notification-${record.severity}${exiting ? " is-exiting" : ""}`}
    role={record.severity === "error" ? "alert" : "status"}
    aria-hidden={exiting}
    inert={exiting}
    aria-label={record.message === "Saving result…" ? "Saving result" : undefined}>
    <span className="notification-severity">{record.severity}</span><span>{record.message}</span>
    <Button type="button" aria-label={`Dismiss ${record.source} notification`} onClick={() => hideNotificationToast(record.id)}>×</Button>
  </div>;
}

export function NotificationViewport() {
  const records = useNotifications();
  const visibleIds = useSyncExternalStore(subscribeNotifications, notificationToastIds, () => emptyToastIds);
  const visible = visibleIds.map((id) => records.find((record) => record.id === id)).filter((record): record is NotificationRecord => Boolean(record));
  return <div className="notification-viewport">
    {visible.map((record) => <NotificationToast key={`${record.id}:${record.active}:${record.severity}:${record.message}`} record={record} />)}
  </div>;
}
