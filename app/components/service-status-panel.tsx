"use client";
import { Button } from "./buttons/BaseButton";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { usePopupKeyboard } from "../lib/keyboard-shortcuts";
import { startExplorerSessionRecovery } from "../lib/explorer-session";
import { API_URL } from "../const";
import { backgroundFetch } from "../lib/background-fetch";
import { clearFinishedActivity } from "../lib/activity-clear-command";
import { requestActivityControl } from "../lib/activity-control-command";
import { browserActivitySnapshot, subscribeBrowserActivity } from "../lib/browser-activity";
import { setBackgroundDiagnostics, setLatestServiceStatus, type ActivityItem, type ActivityResponse } from "../lib/service-status";
import { usesLocalApi } from "../utils/local";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";

const emptyBrowserActivity: ReturnType<typeof browserActivitySnapshot> = [];

function isActivityResponse(value: unknown): value is ActivityResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ActivityResponse>;
  const counts = candidate.counts;
  return Array.isArray(candidate.items)
    && candidate.items.every(item => typeof item.id === "string" && typeof item.source === "string"
      && typeof item.title === "string" && typeof item.state === "string" && typeof item.phase === "string"
      && typeof item.updated_at === "string" && typeof item.paused === "boolean"
      && (item.paused_by_settings === undefined || typeof item.paused_by_settings === "boolean")
      && (item.classification === undefined || Object.hasOwn(classificationLabels, item.classification))
      && (item.stages === undefined || (Array.isArray(item.stages) && isActivityResponse({ items: item.stages, counts: { running: 0, queued: 0, paused: 0, failed: 0 }, total: 0, next_offset: null })))
      && typeof item.promoted === "boolean" && (item.error === null || typeof item.error === "string")
      && (item.completed === null || typeof item.completed === "number")
      && (item.total === null || typeof item.total === "number"))
    && Boolean(counts) && typeof counts?.running === "number" && typeof counts.queued === "number"
    && typeof counts.paused === "number" && typeof counts.failed === "number"
    && (candidate.clearable_finished === undefined || (typeof candidate.clearable_finished === "number" && candidate.clearable_finished >= 0))
    && (candidate.completion_cutoff === undefined || candidate.completion_cutoff === null || typeof candidate.completion_cutoff === "string")
    && (candidate.completion_snapshot === undefined || candidate.completion_snapshot === null || typeof candidate.completion_snapshot === "string")
    && typeof candidate.total === "number"
    && (candidate.next_offset === null || typeof candidate.next_offset === "number");
}

function ProgressBar({ item }: { item: ActivityItem }) {
  if (item.classification === "history") return <p>Last recorded status: {item.state.replaceAll("_", " ")}</p>;
  const hasTotal = item.total !== null && item.total > 0 && item.completed !== null;
  const percentage = hasTotal ? Math.min(100, Math.round((item.completed! / item.total!) * 100)) : null;
  return <div className="activity-progress-wrap">
    {hasTotal ? <progress max={item.total!} value={item.completed!} aria-label={`${item.title} progress`} />
      : <div className={`activity-indeterminate ${["queued", "paused", "complete", "failed"].includes(item.state) ? "is-waiting" : ""}`}
          role="progressbar" aria-label={`${item.title} progress`} aria-valuetext={item.state === "queued" ? "Queued" : item.state === "paused" ? "Paused" : item.phase} />}
    <span>{percentage === null ? item.state === "queued" ? "Waiting" : item.state === "paused" ? "Paused" : item.state === "complete" ? "Finished" : item.state === "failed" ? "Failed" : "In progress" : `${percentage}% · ${item.completed}/${item.total}`}</span>
  </div>;
}

const classificationLabels = { progressing: "Progressing", needs_attention: "Needs attention", waiting: "Waiting", disabled: "Disabled in Settings", paused: "Manually paused", finished: "Finished", history: "History" };
function activityGroup(item: ActivityItem) {
  if (item.classification) return classificationLabels[item.classification];
  const state = item.state;
  if (["running", "leased", "finalizing", "pausing"].includes(state)) return "Running";
  if (["queued", "retrying"].includes(state)) return "Queued";
  if (state === "paused") return "Paused";
  if (state === "failed") return "Needs attention";
  return "Recently completed";
}

function sameActivityItems(left: ActivityItem[], right: ActivityItem[]) {
  return left.length === right.length && left.every((item, index) => {
    const other = right[index];
    return item.source === other.source && item.id === other.id && item.title === other.title
      && item.state === other.state && item.phase === other.phase && item.completed === other.completed
      && item.total === other.total && item.updated_at === other.updated_at && item.error === other.error
      && item.paused === other.paused && Boolean(item.paused_by_settings) === Boolean(other.paused_by_settings)
      && item.promoted === other.promoted && item.classification === other.classification
      && item.health === other.health && item.last_progress_at === other.last_progress_at
      && item.waiting_reason === other.waiting_reason && JSON.stringify(item.stages) === JSON.stringify(other.stages);
  });
}

// Queue depths remain in the diagnostic snapshot; only health and counts render here.
function sameActivitySummary(left: ActivityResponse | null, right: ActivityResponse) {
  return left !== null && left.counts.running === right.counts.running && left.counts.queued === right.counts.queued
    && left.counts.paused === right.counts.paused && left.counts.failed === right.counts.failed
    && left.writer?.healthy === right.writer?.healthy
    && JSON.stringify(left.counts) === JSON.stringify(right.counts)
    && left.completion_cutoff === right.completion_cutoff && left.completion_snapshot === right.completion_snapshot
    && left.clearable_finished === right.clearable_finished;
}

const groupOrder = ["Progressing", "Running", "Needs attention", "Waiting", "Queued", "Disabled in Settings", "Manually paused", "Paused", "Finished", "Recently completed", "History"];

export function ServiceStatusPanel() {
  useEffect(() => usesLocalApi() ? startExplorerSessionRecovery() : undefined, []);
  const lastDiagnosticsRequest = useRef(Number.NEGATIVE_INFINITY);
  const [open, setOpen] = useState(false);
  const popupRef = useRef<HTMLElement>(null);
  usePopupKeyboard(popupRef, () => setOpen(false), open);
  // Null means this mounted panel has never successfully loaded activity; paused reads retain known status.
  const [status, setStatus] = useState<ActivityResponse | null>(null);
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [foregroundWaiting, setForegroundWaiting] = useState(false);
  const requestedForegroundWaiting = useRef(false);
  const publishForegroundWaiting = useCallback((waiting: boolean) => {
    if (requestedForegroundWaiting.current === waiting) return;
    requestedForegroundWaiting.current = waiting;
    setForegroundWaiting(waiting);
  }, []);
  const requestedError = useRef<string | null>(null);
  const publishError = useCallback((message: string | null) => {
    if (requestedError.current === message) return;
    requestedError.current = message;
    setError(message);
  }, []);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [groupFilter, setGroupFilter] = useState("all");
  const [clearing, setClearing] = useState(false);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const refreshActivity = useRef<() => Promise<ActivityResponse | null>>(async () => null);
  const updatePollingDemand = useRef<(open: boolean, offset: number, group: string) => void>(() => undefined);
  const refresh = () => refreshActivity.current();
  const browserItems = useSyncExternalStore(subscribeBrowserActivity, browserActivitySnapshot, () => emptyBrowserActivity);
  useEffect(() => {
    const key = "analysis-activity-error";
    if (error) publishNotification({ severity: "error", source: "analysis activity", key, message: error });
    else if (status !== null) {
      const previous = notifications().find((record) => record.key === key && !record.resolvedAt);
      if (previous) resolveNotification(previous.id, { severity: "success", message: "Analysis activity is available again." });
    }
  }, [error, status]);
  useEffect(() => {
    const key = "database-writer-health";
    if (status?.writer?.healthy === false) publishNotification({ severity: "error", source: "database writer", key,
      message: "The database writer is unavailable. Restart Tempo before making changes." });
    else if (status?.writer?.healthy) {
      const previous = notifications().find((record) => record.key === key && !record.resolvedAt);
      if (previous) resolveNotification(previous.id, { severity: "success", message: "The database writer is available again." });
    }
  }, [status?.writer?.healthy]);


  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let panelOpen = false;
    let currentOffset = 0;
    let currentGroup = "all";
    let offsetGeneration = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: Promise<ActivityResponse | null> | null = null;
    let refreshPending = false;
    let wakeQueued = false;
    let failureCount = 0;
    let foregroundWaitDelayMilliseconds: number | null = null;
    let latest: ActivityResponse | null = null;
    let displayedSummary: ActivityResponse | null = null;
    let displayedItems: ActivityItem[] = [];
    let displayedNextOffset: number | null = null;
    const eligible = () => !stopped && document.visibilityState === "visible" && navigator.onLine;
    const clearTimer = () => { clearTimeout(timer); timer = undefined; };
    const delay = () => {
      if (foregroundWaitDelayMilliseconds !== null) return foregroundWaitDelayMilliseconds;
      if (failureCount) {
        const backoff = [5_000, 10_000, 20_000, 60_000][Math.min(failureCount - 1, 3)];
        return panelOpen ? backoff : Math.max(30_000, backoff);
      }
      // Closed counts/health are at most 30 seconds old plus request duration on success.
      return !panelOpen ? 30_000 : latest && latest.counts.running + latest.counts.queued > 0 ? 2_000 : 15_000;
    };
    const schedule = () => {
      clearTimer();
      if (eligible()) timer = setTimeout(() => void poll(), delay());
    };
    const publishItems = (value: ActivityResponse) => {
      if (!sameActivityItems(displayedItems, value.items)) {
        displayedItems = value.items;
        setItems(value.items);
      }
      if (displayedNextOffset !== value.next_offset) {
        displayedNextOffset = value.next_offset;
        setNextOffset(value.next_offset);
      }
    };
    const publish = (value: ActivityResponse) => {
      setLatestServiceStatus(value);
      if (!sameActivitySummary(displayedSummary, value)) {
        displayedSummary = value;
        setStatus(value);
      }
      if (panelOpen) publishItems(value);
      publishError(null);
    };
    // All timer, wake and command refreshes share this component-owned flight.
    const poll = (): Promise<ActivityResponse | null> => {
      clearTimer();
      if (!eligible()) return Promise.resolve(null);
      if (inFlight) { refreshPending = true; return inFlight; }
      inFlight = (async () => {
        do {
          refreshPending = false;
          const requestGeneration = offsetGeneration;
          try {
            const response = await backgroundFetch(`${API_URL}/api/system/activity?offset=${currentOffset}&limit=50${currentGroup === "all" ? "" : `&group=${currentGroup}`}`);
            if (response.status === 503) {
              const retrySeconds = Number(response.headers.get("Retry-After"));
              const body = await response.clone().json().catch(() => null) as { detail?: string } | null;
              if (body?.detail === "Waiting for foreground activity" && Number.isFinite(retrySeconds) && retrySeconds > 0) {
                if (!stopped && requestGeneration === offsetGeneration) {
                  foregroundWaitDelayMilliseconds = Math.min(60, Math.max(1, retrySeconds)) * 1000;
                  publishForegroundWaiting(true);
                }
                continue;
              }
            }
            if (!response.ok) throw new Error(`Activity status failed: HTTP ${response.status}`);
            const value: unknown = await response.json();
            if (!isActivityResponse(value)) throw new Error("Activity status has an unexpected format");
            if (!stopped && requestGeneration === offsetGeneration) {
              latest = value;
              failureCount = 0;
              foregroundWaitDelayMilliseconds = null;
              publishForegroundWaiting(false);
              publish(value);
              // Retain current main's activity-triggered diagnostics read and 15-second minimum.
              if (eligible() && performance.now() - lastDiagnosticsRequest.current >= 15_000) {
                lastDiagnosticsRequest.current = performance.now();
                try {
                  const diagnosticsResponse = await backgroundFetch(`${API_URL}/api/system/background-diagnostics`);
                  if (!diagnosticsResponse.ok) throw new Error("Background diagnostics unavailable");
                  const diagnostics: unknown = await diagnosticsResponse.json();
                  if (!stopped) setBackgroundDiagnostics(diagnostics);
                } catch {
                  if (!stopped) setBackgroundDiagnostics(null);
                }
              }
            }
          } catch (cause) {
            if (!stopped && requestGeneration === offsetGeneration) {
              foregroundWaitDelayMilliseconds = null;
              publishForegroundWaiting(false);
              failureCount += 1;
              const message = cause instanceof Error ? cause.message : "Could not load activity status";
              publishError(message);
            }
          }
        } while (refreshPending && eligible());
        return latest;
      })().finally(() => { inFlight = null; schedule(); });
      return inFlight;
    };
    const recover = () => {
      clearTimer();
      if (eligible()) {
        failureCount = 0;
        if (!wakeQueued) {
          wakeQueued = true;
          queueMicrotask(() => { wakeQueued = false; if (eligible()) void poll(); });
        }
      } else refreshPending = false;
    };
    refreshActivity.current = async () => { failureCount = 0; return poll(); };
    updatePollingDemand.current = (nextOpen, nextOffset, nextGroup) => {
      const opened = nextOpen && !panelOpen;
      const offsetChanged = nextOffset !== currentOffset || nextGroup !== currentGroup;
      currentGroup = nextGroup;
      panelOpen = nextOpen;
      currentOffset = nextOffset;
      if (offsetChanged) { offsetGeneration += 1; latest = null; }
      // Opening while suspended can display cached details without claiming fresh service recovery.
      if (opened && latest && !eligible()) publishItems(latest);
      if (opened || offsetChanged) { failureCount = 0; void poll(); }
      else if (!inFlight) schedule();
    };
    void poll();
    window.addEventListener("focus", recover);
    window.addEventListener("online", recover);
    window.addEventListener("offline", recover);
    document.addEventListener("visibilitychange", recover);
    return () => {
      stopped = true;
      refreshPending = false;
      clearTimer();
      refreshActivity.current = async () => null;
      updatePollingDemand.current = () => undefined;
      window.removeEventListener("focus", recover);
      window.removeEventListener("online", recover);
      window.removeEventListener("offline", recover);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [publishError, publishForegroundWaiting]);
  useEffect(() => { updatePollingDemand.current(open, offset, groupFilter); }, [open, offset, groupFilter]);

  const control = async (item: ActivityItem, action: string) => {
    const key = `${item.source}:${item.id}`;
    setBusyKey(key);
    try {
      await requestActivityControl(item.source, item.id, action);
      window.dispatchEvent(new CustomEvent("tempo:background-control", { detail: { source: item.source, id: item.id, action } }));
      await refresh();
    } catch (cause) {
      publishError(cause instanceof Error ? cause.message : "Activity control failed");
    } finally { setBusyKey(null); }
  };

  const retry = async (item: ActivityItem) => {
    const path = item.source === "durable" ? `/api/system/tasks/${encodeURIComponent(item.id)}/retry`
      : item.source === "threat_analysis" ? `/api/defensive-threats/analysis/${encodeURIComponent(item.id)}/retry`
      : `/api/games/analysis/${encodeURIComponent(item.id)}/retry`;
    setBusyKey(`${item.source}:${item.id}`);
    try {
      const response = await fetch(`${API_URL}${path}`, { method: "POST" });
      if (!response.ok) throw new Error(`Retry failed: HTTP ${response.status}`);
      await refresh();
    } catch (cause) {
      publishError(cause instanceof Error ? cause.message : "Retry failed");
    } finally { setBusyKey(null); }
  };

  const clearFinished = async () => {
    if (!status?.completion_cutoff || !status.completion_snapshot || clearing) return;
    setClearing(true);
    try {
      await clearFinishedActivity({ completion_cutoff: status.completion_cutoff, completion_snapshot: status.completion_snapshot });
      await refresh();
    } catch (cause) { publishError(cause instanceof Error ? cause.message : "Clear finished failed"); }
    finally { setClearing(false); }
  };
  const renderActivity = (item: ActivityItem): React.ReactNode => {
    const key = `${item.source}:${item.id}`;
    const children = item.stages ?? [];
    const controlEligible = !children.length && item.classification !== "history" && item.source !== "study" && item.state !== "complete" && item.state !== "failed";
    return <article key={key} className="tempo-activity-item" data-source={item.source}>
      <div className="tempo-activity-item-heading"><strong>{item.title}</strong><span>{item.classification ? classificationLabels[item.classification] : item.state.replaceAll("_", " ")}</span></div>
      <p>{item.phase.replaceAll("_", " ")}{item.error ? ` · ${item.error}` : ""}</p>
      <ProgressBar item={item} />
      {item.waiting_reason === "retry_delay" && <p>Waiting for the retry delay to end.</p>}
      <time dateTime={item.updated_at}>Updated {item.updated_at.replace("T", " ").slice(0, 16)} UTC</time>
      {controlEligible && item.paused_by_settings && <p>Paused in Settings. Enable Defensive analysis in Settings to allow this work.</p>}
      {controlEligible && <div className="tempo-activity-actions">
        {!item.paused_by_settings && <Button type="button" disabled={busyKey === key} onClick={() => void control(item, item.paused ? "resume" : "pause")}>{item.paused ? "Resume" : "Pause"}</Button>}
        <Button type="button" disabled={busyKey === key} onClick={() => void control(item, item.promoted ? "normal" : "prioritize")}>{item.promoted ? "Normal priority" : "Prioritize"}</Button>
      </div>}
      {!children.length && item.classification !== "history" && item.state === "failed" && ["durable", "game_analysis", "threat_analysis"].includes(item.source) &&
        <Button type="button" disabled={busyKey === key} onClick={() => void retry(item)}>Retry {item.title}</Button>}
      {children.length > 0 && <details><summary>{children.length} stages</summary>{children.map(stage => renderActivity({ ...stage, classification: item.classification === "history" ? "history" : undefined }))}</details>}
    </article>;
  };

  const visibleItems = useMemo(() => {
    if (!open) return [];
    const localItems: ActivityItem[] = browserItems.filter(item => item.state !== "complete").map(item => ({
      source: "study", id: item.id, title: item.title, state: item.state,
      phase: item.phase, completed: null, total: null, updated_at: item.updated_at,
      error: item.error ?? null, paused: false, paused_by_settings: false, promoted: false,
    }));
    return [...items, ...localItems].sort((left, right) =>
      groupOrder.indexOf(activityGroup(left)) - groupOrder.indexOf(activityGroup(right))
      || right.updated_at.localeCompare(left.updated_at));
  }, [open, items, browserItems]);
  const activeCount = (status?.counts.running ?? 0) + (status?.counts.queued ?? 0)
    + browserItems.filter(item => item.state === "running" || item.state === "queued").length;

  return <aside className="tempo-activity-tray">
    <Button type="button" className="tempo-activity-trigger" aria-label="Analysis activity" aria-expanded={open} aria-controls="tempo-activity-content"
      onClick={event => { event.currentTarget.focus(); setOpen(value => !value); }}>
      <span className="tempo-activity-trigger-desktop">Analysis activity</span>
      <span className="tempo-activity-trigger-mobile">Jobs</span>
      {activeCount > 0 && <span> · {activeCount}</span>}
      {((status?.counts.failed ?? 0) > 0 || status?.writer?.healthy === false || error) && <span className="tempo-activity-attention" aria-label="needs attention"> !</span>}
    </Button>
    {open && <section ref={popupRef} id="tempo-activity-content" className="tempo-activity-content" aria-label="Analysis activity">
      <div className="tempo-activity-heading"><strong>Background activity</strong><Button type="button" onClick={() => setOpen(false)}>Close</Button></div>
      {!usesLocalApi() && <p>This practice demo has no local analysis service.</p>}
      {foregroundWaiting && <p role="status">Activity status will refresh after study activity settles.</p>}
      {error && <p role="alert">{error} <Button type="button" onClick={() => void refresh()}>Retry status</Button></p>}
      {status?.writer?.healthy === false && <p role="alert">The database writer is unavailable. Restart Tempo before making changes.</p>}
      {status && (status.clearable_finished === undefined
        ? <p>{status.counts.running} running · {status.counts.queued} queued · {status.counts.paused} paused · {status.counts.failed} failed</p>
        : <p>{status.counts.running} progressing · {status.counts.queued} waiting · {status.counts.failed} need attention · {status.counts.disabled ?? 0} disabled in Settings · {status.counts.manual_paused ?? 0} manually paused</p>)}
      {!error && !foregroundWaiting && status === null && usesLocalApi() && <p>Activity status has not been loaded yet.</p>}
      {!error && status !== null && visibleItems.length === 0 && <p>No background activity yet.</p>}
      {status?.clearable_finished !== undefined && <div className="tempo-activity-filters">
        <label>Show <select aria-label="Activity group" value={groupFilter} onChange={event => { setOffset(0); setGroupFilter(event.target.value); }}>
          <option value="all">All work</option>
          {Object.entries(classificationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <Button type="button" disabled={clearing || !status.clearable_finished || !status.completion_cutoff || !status.completion_snapshot}
          onClick={() => void clearFinished()}>{clearing ? "Checking clear…" : "Clear finished"}</Button>
      </div>}
      <div className="tempo-activity-list">
        {groupOrder.map(label => {
          const members = visibleItems.filter(item => activityGroup(item) === label);
          if (!members.length) return null;
          const content = members.map(item => renderActivity(item));
          return ["Disabled in Settings", "Manually paused", "Finished", "History"].includes(label)
            ? <details key={label} className="tempo-activity-group"><summary>{label} · {members.length}</summary>{content}</details>
            : <section key={label} aria-label={label}><h3>{label}</h3>{content}</section>;
        })}
      </div>
      <div className="tempo-activity-pages">
        {offset > 0 && <Button type="button" onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>}
        {nextOffset !== null && <Button type="button" onClick={() => setOffset(nextOffset)}>Show more</Button>}
      </div>
    </section>}
  </aside>;
}
