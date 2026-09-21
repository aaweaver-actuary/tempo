"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL } from "../const";
import { backgroundFetch } from "../lib/background-fetch";
import {
  setLatestServiceStatus,
  type ServiceStatus,
} from "../lib/service-status";
import { usesLocalApi } from "../utils/local";

function isServiceStatus(value: unknown): value is ServiceStatus {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ServiceStatus>;
  return Array.isArray(candidate.tasks) && Boolean(candidate.counts) && Boolean(candidate.writer);
}

export function ServiceStatusPanel() {
  const [status, setStatus] = useState<ServiceStatus | null>(null);

  const refresh = useCallback(async () => {
    if (!usesLocalApi()) return null;
    try {
      const response = await backgroundFetch(`${API_URL}/api/system/tasks`);
      if (!response.ok) return null;
      const value: unknown = await response.json();
      if (!isServiceStatus(value)) return null;
      setStatus(value);
      setLatestServiceStatus(value);
      return value;
    } catch {
      // The primary workspace error handling remains authoritative.
      return null;
    }
  }, []);

  useEffect(() => {
    if (!usesLocalApi()) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      const currentStatus = await refresh();
      if (!cancelled) {
        const active =
          (currentStatus?.counts.active ?? 0) +
            (currentStatus?.counts.queued ?? 0) >
          0;
        timer = setTimeout(poll, active ? 2_000 : 15_000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refresh]);

  if (!status) return null;
  const failures = status.tasks.filter((task) => task.state === "failed");
  const activeCount = status.counts.active + status.counts.queued;
  if (failures.length === 0 && activeCount === 0 && status.writer.healthy) return null;

  return (
    <aside className="tempo-service-status" aria-live="polite">
      <strong>{failures.length ? "Background work needs attention" : "Refreshing derived data"}</strong>
      {!status.writer.healthy && <p>The database writer is unavailable. Restart Tempo before making changes.</p>}
      {activeCount > 0 && <p>{activeCount} durable task{activeCount === 1 ? "" : "s"} queued or active.</p>}
      {failures.map((task) => (
        <div key={task.id}>
          <span>{task.kind}: {task.last_error ?? "Task failed"}</span>
          <button
            type="button"
            onClick={() => {
              void fetch(`${API_URL}/api/system/tasks/${encodeURIComponent(task.id)}/retry`, {
                method: "POST",
              }).then(refresh);
            }}
          >
            Retry
          </button>
        </div>
      ))}
    </aside>
  );
}
