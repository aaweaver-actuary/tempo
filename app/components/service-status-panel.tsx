"use client";

import { Fragment, useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { API_URL } from "../const";
import { backgroundFetch } from "../lib/background-fetch";
import { browserActivitySnapshot, subscribeBrowserActivity } from "../lib/browser-activity";
import { setLatestServiceStatus, type ActivityItem, type ActivityResponse } from "../lib/service-status";
import { usesLocalApi } from "../utils/local";

const emptyBrowserActivity: ReturnType<typeof browserActivitySnapshot> = [];

function isActivityResponse(value: unknown): value is ActivityResponse {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ActivityResponse>;
  const counts = candidate.counts;
  return Array.isArray(candidate.items)
    && candidate.items.every(item => typeof item.id === "string" && typeof item.source === "string"
      && typeof item.title === "string" && typeof item.state === "string" && typeof item.phase === "string"
      && typeof item.updated_at === "string" && typeof item.paused === "boolean"
      && typeof item.promoted === "boolean" && (item.error === null || typeof item.error === "string")
      && (item.completed === null || typeof item.completed === "number")
      && (item.total === null || typeof item.total === "number"))
    && Boolean(counts) && typeof counts?.running === "number" && typeof counts.queued === "number"
    && typeof counts.paused === "number" && typeof counts.failed === "number"
    && typeof candidate.total === "number"
    && (candidate.next_offset === null || typeof candidate.next_offset === "number");
}

function ProgressBar({ item }: { item: ActivityItem }) {
  const hasTotal = item.total !== null && item.total > 0 && item.completed !== null;
  const percentage = hasTotal ? Math.min(100, Math.round((item.completed! / item.total!) * 100)) : null;
  return <div className="activity-progress-wrap">
    {hasTotal ? <progress max={item.total!} value={item.completed!} aria-label={`${item.title} progress`} />
      : <div className={`activity-indeterminate ${["queued", "paused", "complete", "failed"].includes(item.state) ? "is-waiting" : ""}`}
          role="progressbar" aria-label={`${item.title} progress`} aria-valuetext={item.state === "queued" ? "Queued" : item.state === "paused" ? "Paused" : item.phase} />}
    <span>{percentage === null ? item.state === "queued" ? "Waiting" : item.state === "paused" ? "Paused" : item.state === "complete" ? "Finished" : item.state === "failed" ? "Failed" : "In progress" : `${percentage}% · ${item.completed}/${item.total}`}</span>
  </div>;
}

function activityGroup(state: string) {
  if (["running", "leased", "finalizing", "pausing"].includes(state)) return "Running";
  if (["queued", "retrying"].includes(state)) return "Queued";
  if (state === "paused") return "Paused";
  if (state === "failed") return "Needs attention";
  return "Recently completed";
}

const groupOrder = ["Running", "Queued", "Paused", "Needs attention", "Recently completed"];

export function ServiceStatusPanel() {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<ActivityResponse | null>(null);
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const browserItems = useSyncExternalStore(subscribeBrowserActivity, browserActivitySnapshot, () => emptyBrowserActivity);

  const refresh = useCallback(async () => {
    if (!usesLocalApi()) return null;
    try {
      const response = await backgroundFetch(`${API_URL}/api/system/activity?offset=${offset}&limit=50`);
      if (!response.ok) throw new Error(`Activity status failed: HTTP ${response.status}`);
      const value: unknown = await response.json();
      if (!isActivityResponse(value)) throw new Error("Activity status has an unexpected format");
      setStatus(value);
      setLatestServiceStatus(value);
      setItems(value.items);
      setNextOffset(value.next_offset);
      setError(null);
      return value;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load activity status");
      return null;
    }
  }, [offset]);

  useEffect(() => {
    if (!usesLocalApi()) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const current = await refresh();
      if (!cancelled) timer = setTimeout(poll, current && current.counts.running + current.counts.queued > 0 ? 2_000 : 15_000);
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [refresh]);

  const control = async (item: ActivityItem, action: string) => {
    const key = `${item.source}:${item.id}`;
    setBusyKey(key);
    try {
      const response = await fetch(`${API_URL}/api/system/activity/control`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: item.source, id: item.id, action }),
      });
      if (!response.ok) throw new Error(`Could not ${action} ${item.title}: HTTP ${response.status}`);
      window.dispatchEvent(new CustomEvent("tempo:background-control", { detail: { source: item.source, id: item.id, action } }));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Activity control failed");
    } finally { setBusyKey(null); }
  };

  const retry = async (item: ActivityItem) => {
    const path = item.source === "durable" ? `/api/system/tasks/${encodeURIComponent(item.id)}/retry`
      : `/api/games/analysis/${encodeURIComponent(item.id)}/retry`;
    setBusyKey(`${item.source}:${item.id}`);
    try {
      const response = await fetch(`${API_URL}${path}`, { method: "POST" });
      if (!response.ok) throw new Error(`Retry failed: HTTP ${response.status}`);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Retry failed");
    } finally { setBusyKey(null); }
  };

  const localItems: ActivityItem[] = browserItems.map(item => ({
    source: "study", id: item.id, title: item.title, state: item.state,
    phase: item.phase, completed: null, total: null, updated_at: item.updated_at,
    error: item.error ?? null, paused: false, promoted: false,
  }));
  const visibleItems = [...items, ...localItems].sort((left, right) =>
    groupOrder.indexOf(activityGroup(left.state)) - groupOrder.indexOf(activityGroup(right.state))
    || right.updated_at.localeCompare(left.updated_at));
  const activeCount = (status?.counts.running ?? 0) + (status?.counts.queued ?? 0)
    + localItems.filter(item => item.state === "running" || item.state === "queued").length;

  return <aside className="tempo-activity-tray">
    <button type="button" className="tempo-activity-trigger" aria-label="Analysis activity" aria-expanded={open} aria-controls="tempo-activity-content"
      onClick={() => setOpen(value => !value)}>
      <span className="tempo-activity-trigger-desktop">Analysis activity</span>
      <span className="tempo-activity-trigger-mobile">Activity</span>
      {activeCount > 0 && <span> · {activeCount}</span>}
      {((status?.counts.failed ?? 0) > 0 || status?.writer?.healthy === false || error) && <span className="tempo-activity-attention" aria-label="needs attention"> !</span>}
    </button>
    {open && <section id="tempo-activity-content" className="tempo-activity-content" aria-label="Analysis activity">
      <div className="tempo-activity-heading"><strong>Background activity</strong><button type="button" onClick={() => setOpen(false)}>Close</button></div>
      {!usesLocalApi() && <p>This practice demo has no local analysis service.</p>}
      {error && <p role="alert">{error} <button type="button" onClick={() => void refresh()}>Retry status</button></p>}
      {status?.writer?.healthy === false && <p role="alert">The database writer is unavailable. Restart Tempo before making changes.</p>}
      {status && <p>{status.counts.running} running · {status.counts.queued} queued · {status.counts.paused} paused · {status.counts.failed} failed</p>}
      {!error && visibleItems.length === 0 && <p>No background activity yet.</p>}
      <div className="tempo-activity-list">
        {visibleItems.map((item, index) => {
          const key = `${item.source}:${item.id}`;
          const controlEligible = item.source !== "study" && item.state !== "complete" && item.state !== "failed";
          const group = activityGroup(item.state);
          return <Fragment key={key}>
            {(index === 0 || activityGroup(visibleItems[index - 1].state) !== group) && <h3>{group}</h3>}
            <article className="tempo-activity-item">
            <div className="tempo-activity-item-heading"><strong>{item.title}</strong><span>{item.state.replaceAll("_", " ")}</span></div>
            <p>{item.phase.replaceAll("_", " ")}{item.error ? ` · ${item.error}` : ""}</p>
            <ProgressBar item={item} />
            <time dateTime={item.updated_at}>Updated {item.updated_at.replace("T", " ").slice(0, 16)} UTC</time>
            {controlEligible && <div className="tempo-activity-actions">
              <button type="button" disabled={busyKey === key} onClick={() => void control(item, item.paused ? "resume" : "pause")}>{item.paused ? "Resume" : "Pause"}</button>
              <button type="button" disabled={busyKey === key} onClick={() => void control(item, item.promoted ? "normal" : "prioritize")}>{item.promoted ? "Normal priority" : "Prioritize"}</button>
            </div>}
            {item.state === "failed" && (item.source === "durable" || item.source === "game_analysis") &&
              <button type="button" disabled={busyKey === key} onClick={() => void retry(item)}>Retry {item.title}</button>}
            </article>
          </Fragment>;
        })}
      </div>
      <div className="tempo-activity-pages">
        {offset > 0 && <button type="button" onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button>}
        {nextOffset !== null && <button type="button" onClick={() => setOffset(nextOffset)}>Show more</button>}
      </div>
    </section>}
  </aside>;
}
