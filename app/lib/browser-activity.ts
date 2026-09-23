export type BrowserActivity = {
  id: string;
  title: string;
  state: "queued" | "running" | "complete" | "failed";
  phase: string;
  updated_at: string;
  error?: string;
};

const entries = new Map<string, BrowserActivity>();
const listeners = new Set<() => void>();
let snapshot: BrowserActivity[] = [];

function publish() {
  const ordered = [...entries.values()].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
  snapshot = [
    ...ordered.filter(item => item.state === "queued" || item.state === "running"),
    ...ordered.filter(item => item.state === "complete" || item.state === "failed").slice(0, 25),
  ];
  for (const listener of listeners) listener();
}

export function updateBrowserActivity(id: string, title: string, state: BrowserActivity["state"], phase: string, error?: string) {
  entries.set(id, { id, title, state, phase, error, updated_at: new Date().toISOString() });
  if (entries.size > 50) {
    for (const [key, value] of entries) {
      if (entries.size <= 50) break;
      if (value.state === "complete" || value.state === "failed") entries.delete(key);
    }
  }
  publish();
}

export function subscribeBrowserActivity(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function browserActivitySnapshot() { return snapshot; }

export function clearBrowserActivity() {
  entries.clear();
  publish();
}
