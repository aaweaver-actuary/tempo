import { API_URL } from "../const";
import { activityNotificationPageSchema } from "../domain/schemas/activity-notifications";
import { backgroundFetch } from "./background-fetch";
import { importActivityIncident } from "./notifications";

const CURSOR_KEY = "tempo-activity-notification-cursor-v1";
const POLL_MILLISECONDS = 60_000;
type Cursor = { workspaceId: string; sequence: number };

function loadCursor(): Cursor | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(CURSOR_KEY) ?? "null");
    if (value && typeof value === "object" && "workspaceId" in value && "sequence" in value &&
        typeof value.workspaceId === "string" && typeof value.sequence === "number" &&
        Number.isSafeInteger(value.sequence) && value.sequence >= 0)
      return { workspaceId: value.workspaceId, sequence: value.sequence };
  } catch { /* Damaged preferences must not prevent a fresh server read. */ }
  return null;
}

/** Single owner, bounded pages, and quiet polling while the browser is hidden. */
export function startActivityNotificationPolling(onAvailability: (available: boolean | null) => void): () => void {
  let cursor = loadCursor();
  let stopped = false;
  let active = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  function schedule(delay = POLL_MILLISECONDS) {
    if (stopped) return;
    clearTimeout(timer);
    timer = setTimeout(() => void poll(), delay);
  }
  async function poll() {
    if (stopped || active) return;
    if (document.visibilityState === "hidden") { schedule(); return; }
    active = true;
    controller = new AbortController();
    const timeout = setTimeout(() => controller?.abort(), 5_000);
    let delay = POLL_MILLISECONDS;
    try {
      const previousSequence = cursor?.sequence ?? 0;
      const response = await backgroundFetch(`${API_URL}/api/system/activity/notifications?after=${previousSequence}&limit=50`, { signal: controller.signal });
      if (response.status === 503 && response.headers.get("Retry-After")) {
        const body = await response.clone().json().catch(() => null) as { detail?: string } | null;
        if (body?.detail === "Waiting for foreground activity") {
          onAvailability(null); delay = Math.min(60, Math.max(1, Number(response.headers.get("Retry-After")) || 60)) * 1000;
          return;
        }
      }
      if (!response.ok) throw new Error("Analysis monitoring is unavailable");
      const page = activityNotificationPageSchema.parse(await response.json());
      if (stopped) return;
      if (cursor && cursor.workspaceId !== page.workspace_id) {
        // A restored/different workspace has its own sequence. Read it from zero.
        cursor = { workspaceId: page.workspace_id, sequence: 0 }; delay = 0; return;
      }
      if (page.items.some(item => item.sequence <= previousSequence) || page.next_cursor < previousSequence)
        throw new Error("Analysis notification cursor moved backwards");
      for (const incident of page.items) importActivityIncident(page.workspace_id, incident);
      cursor = { workspaceId: page.workspace_id, sequence: page.next_cursor };
      try { localStorage.setItem(CURSOR_KEY, JSON.stringify(cursor)); } catch { /* Retain the in-session cursor. */ }
      onAvailability(page.monitoring_available);
      if (page.has_more) delay = 250;
    } catch {
      if (!stopped) onAvailability(false);
    } finally {
      clearTimeout(timeout); active = false; controller = undefined; schedule(delay);
    }
  }
  function wake() { if (!stopped && !active) { clearTimeout(timer); void poll(); } }
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("online", wake);
  void poll();
  return () => {
    stopped = true; clearTimeout(timer); controller?.abort();
    document.removeEventListener("visibilitychange", wake); window.removeEventListener("online", wake);
  };
}
