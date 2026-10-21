/** Existing durable browser persistence; application decisions stay with callers. */
export function pendingActivityControlKey(source: string, workId: string) {
  return `tempo-pending-activity-control-v1:${source}:${workId}`;
}
export function readPendingActivityControl(key: string) { return localStorage.getItem(key); }
export function writePendingActivityControl(key: string, pending: unknown) { localStorage.setItem(key, JSON.stringify(pending)); }
export function removePendingActivityControl(key: string) { localStorage.removeItem(key); }
