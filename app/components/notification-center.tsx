"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "./buttons/BaseButton";
import {
  buildNotificationExport, clearAllNotifications, clearNotificationGroup, hideNotificationToast, hydrateNotifications,
  groupNotifications, notificationNeedsAttention, notificationToasts, notifications,
  subscribeNotifications, NOTIFICATION_HISTORY_LIMIT,
  type NotificationRecord, type NotificationGroup, type NotificationToast as ToastState,
} from "../lib/notifications";
import { buildDebugBundle, copyDebugBundle, debugErrors } from "../lib/debug-reporting";

import { usesLocalApi } from "../utils/local";
import { startActivityNotificationPolling } from "../lib/activity-notifications";

import { usePopupKeyboard } from "../lib/keyboard-shortcuts";

type Threshold = "info" | "warning" | "error";
const emptyNotificationRecords: readonly NotificationRecord[] = [];
const emptyToasts: readonly ToastState[] = [];

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
  const [monitoringAvailable, setMonitoringAvailable] = useState<boolean | null>(null);
  useEffect(() => usesLocalApi() ? startActivityNotificationPolling(setMonitoringAvailable) : undefined, []);
  const [open, setOpen] = useState(false);
  const popupRef = useRef<HTMLElement>(null);
  usePopupKeyboard(popupRef, () => setOpen(false), open);
  const [filter, setFilter] = useState<"attention" | "all" | NotificationRecord["severity"]>("attention");
  const [threshold, setThreshold] = useState<Threshold>("info");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const [debugCopyId, setDebugCopyId] = useState<string>();
  const [debugCopyState, setDebugCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const groups = groupNotifications(records);
  const shown = groups.filter(({ record }) => filter === "all" ||
    (filter === "attention" ? notificationNeedsAttention(record) : record.severity === filter));
  const exportText = buildNotificationExport(threshold);
  const needsAttention = groups.filter(({ record }) => notificationNeedsAttention(record)).length;

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
      aria-expanded={open} aria-controls="notification-tray" onClick={event => {
        event.currentTarget.focus();
        if (!open) setFilter("attention");
        setOpen((value) => !value);
      }}>
      <span aria-hidden="true">🔔</span><span className="notification-trigger-label">Notifications</span>
      {needsAttention > 0 && <span className="notification-count">{needsAttention}</span>}
    </Button>
    {open && <section ref={popupRef} id="notification-tray" className="notification-tray" aria-label="Notifications">
      <header><strong>Notifications</strong><div className="notification-header-actions">
        <Button type="button" disabled={!records.some((record) => !record.clearedAt)}
          onClick={clearAllNotifications}>Clear all</Button>
        <Button type="button" onClick={() => setOpen(false)}>Close</Button>
      </div></header>
      {monitoringAvailable === false && <p role="status">Analysis monitoring is unavailable. Its health is unknown; retry when the service is available.</p>}
      <p className="notification-retention">Newest first · latest {NOTIFICATION_HISTORY_LIMIT} kept on this device</p>
      <div className="notification-filters" aria-label="Filter notifications">
        {(["attention", "all", "error", "warning", "success", "info"] as const).map((severity) =>
          <Button type="button" key={severity} aria-pressed={filter === severity}
            onClick={() => setFilter(severity)}>{severity === "attention" ? "Needs attention" : severity === "all" ? "All" : severity}</Button>)}
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
        {shown.map(({ key, record, occurrenceCount }) => <article key={key} className={`notification-item notification-${record.severity}`}>
          <div className="notification-item-meta"><span className="notification-severity">{record.severity}</span>
            <time dateTime={record.updatedAt}>{new Date(record.updatedAt).toLocaleString()}</time></div>
          {occurrenceCount > 1 && <p className="notification-retention">Repeated {occurrenceCount} times</p>}
          <strong>{record.source}</strong><p>{record.message}</p><NotificationDetails record={record} />
          {typeof record.details?.activityWorkId === "string" && <Button type="button" onClick={() => {
            setOpen(false); window.dispatchEvent(new CustomEvent("tempo:open-activity", { detail: { source: record.details?.activitySource, id: record.details?.activityWorkId } }));
          }}>View analysis</Button>}
          <div className="notification-clear-actions">
            {record.clearedAt ? <span>Cleared</span> : <>
              {notificationNeedsAttention(record) && <span className="notification-new-label">New</span>}
              <Button type="button" onClick={() => clearNotificationGroup(key)}>Clear</Button>
            </>}
          </div>
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

function NotificationToast({ group, toastId, order }: { group: NotificationGroup; toastId: string; order: number }) {
  const { record, occurrenceCount } = group;
  const [exiting, setExiting] = useState(false);
  const toastRef = useRef<HTMLDivElement>(null);
  usePopupKeyboard(toastRef, () => hideNotificationToast(toastId), !exiting, true, order);
  useEffect(() => {
    const fadeTimer = window.setTimeout(() => setExiting(true), 7_000);
    const hideTimer = window.setTimeout(() => hideNotificationToast(toastId), 7_250);
    return () => { window.clearTimeout(fadeTimer); window.clearTimeout(hideTimer); };
  }, [toastId]);
  return <div ref={toastRef} className={`notification-toast notification-${record.severity}${exiting ? " is-exiting" : ""}`}
    role={record.severity === "error" ? "alert" : "status"}
    aria-hidden={exiting}
    inert={exiting}>
    <span className="notification-severity">{record.severity}</span><span>{record.message}
      {occurrenceCount > 1 && <small className="notification-repeat-count">Repeated {occurrenceCount} times</small>}</span>
    <Button type="button" aria-label={`Dismiss ${record.source} notification`} onClick={() => hideNotificationToast(toastId)}>×</Button>
  </div>;
}

export function NotificationViewport() {
  const records = useNotifications();
  const visibleToasts = useSyncExternalStore(subscribeNotifications, notificationToasts, () => emptyToasts);
  const groups = groupNotifications(records);
  return <div className="notification-viewport">
    {visibleToasts.map((toast, order) => {
      const group = groups.find((candidate) => candidate.key === toast.groupKey);
      return group && <NotificationToast key={toast.id} group={group} toastId={toast.id} order={order} />;
    })}
  </div>;
}
