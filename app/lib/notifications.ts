export type NotificationSeverity = "info" | "success" | "warning" | "error";

export type NotificationRecord = {
  id: string;
  key?: string;
  occurredAt: string;
  updatedAt: string;
  occurrenceCount?: number;
  resolvedAt: string | null;
  active: boolean;
  severity: NotificationSeverity;
  source: string;
  message: string;
  details?: Record<string, string | number | boolean | string[]>;
};

export type NotificationInput = Pick<NotificationRecord, "severity" | "source" | "message"> &
  Partial<Pick<NotificationRecord, "key" | "active" | "details">>;

export const NOTIFICATION_HISTORY_LIMIT = 500;
const STORAGE_KEY = "tempo-notifications-v1";
const severityRank: Record<NotificationSeverity, number> = {
  info: 0, success: 0, warning: 1, error: 2,
};
const subscribers = new Set<() => void>();
let history: readonly NotificationRecord[] = [];
let visibleToastIds: readonly string[] = [];
let nextId = 1;
let loaded = false;

function notifySubscribers() {
  subscribers.forEach((subscriber) => subscriber());
}

export function sanitizeNotificationText(value: string): string {
  return value
    .replace(/https?:\/\/[^\s)]+/gi, (url) => {
      try { return new URL(url).pathname; } catch { return "[redacted-url]"; }
    })
    .replace(/[?&][A-Za-z0-9_.-]+=[^\s&]+/g, (parameter) => `${parameter.slice(0, parameter.indexOf("=") + 1)}[redacted]`)
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
    .replace(/(bearer\s+)[A-Za-z0-9._~+/-]+/gi, "$1[redacted]")
    .replace(/\b(token|api[_-]?key|password|secret|passphrase|authorization)\s*[:=]\s*[^\s,;]+/gi, "$1=[redacted]")
    .slice(0, 2_000);
}

function safeDetails(details: NotificationInput["details"]): NotificationRecord["details"] {
  if (!details) return undefined;
  return Object.fromEntries(Object.entries(details).slice(0, 30).map(([name, value]) => [
    sanitizeNotificationText(name).slice(0, 80),
    Array.isArray(value) ? value.slice(0, 500).map(sanitizeNotificationText)
      : typeof value === "string" ? sanitizeNotificationText(value)
        : typeof value === "number" || typeof value === "boolean" ? value : "[unsupported detail]",
  ]));
}

function persist() {
  if (typeof localStorage === "undefined") return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(history)); } catch {
    // Notifications still work for the current session when storage is full or disabled.
  }
}

export function hydrateNotifications() {
  if (loaded || typeof localStorage === "undefined") return;
  loaded = true;
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    if (!Array.isArray(stored)) return;
    const restored = stored.filter((item): item is NotificationRecord =>
      item && typeof item.id === "string" && typeof item.message === "string" &&
      typeof item.source === "string" && typeof item.updatedAt === "string" &&
      ["info", "success", "warning", "error"].includes(item.severity))
      .slice(0, NOTIFICATION_HISTORY_LIMIT)
      .map((item) => ({ ...item, active: false, occurrenceCount: item.occurrenceCount ?? 1,
        message: sanitizeNotificationText(item.message), details: safeDetails(item.details) }));
    history = [...history, ...restored.filter((item) => !history.some((current) => current.id === item.id))]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, NOTIFICATION_HISTORY_LIMIT);
    notifySubscribers();
  } catch {
    // A damaged history must not prevent the app from opening.
  }
}

export function subscribeNotifications(subscriber: () => void) {
  subscribers.add(subscriber);
  return () => subscribers.delete(subscriber);
}

export function notifications() { return history; }
export function notificationToastIds() { return visibleToastIds; }

function showToast(id: string) {
  const newest = [id, ...visibleToastIds.filter((visibleId) => visibleId !== id)];
  const active = newest.filter((visibleId) => history.find((record) => record.id === visibleId)?.active);
  const selected = new Set([...active, ...newest.filter((visibleId) => !active.includes(visibleId))].slice(0, 3));
  visibleToastIds = newest.filter((visibleId) => selected.has(visibleId));
  notifySubscribers();
}

export function hideNotificationToast(id: string) {
  if (!visibleToastIds.includes(id)) return;
  visibleToastIds = visibleToastIds.filter((visibleId) => visibleId !== id);
  notifySubscribers();
}

export function publishNotification(input: NotificationInput): string {
  hydrateNotifications();
  const message = sanitizeNotificationText(input.message);
  const existing = input.key && history.find((record) => record.key === input.key && !record.resolvedAt);
  if (existing && existing.message === message && existing.severity === input.severity) {
    const observed = { ...existing, occurrenceCount: (existing.occurrenceCount ?? 1) + 1,
      updatedAt: new Date().toISOString(), details: safeDetails(input.details) ?? existing.details };
    history = [observed, ...history.filter((record) => record.id !== existing.id)]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    persist();
    notifySubscribers();
    return existing.id;
  }
  if (existing) {
    updateNotification(existing.id, input);
    return existing.id;
  }
  const now = new Date().toISOString();
  const record: NotificationRecord = {
    id: typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `notification-${crypto.randomUUID()}` : `notification-${Date.now()}-${nextId++}`,
    key: input.key,
    occurredAt: now,
    updatedAt: now,
    occurrenceCount: 1,
    resolvedAt: null,
    active: input.active ?? false,
    severity: input.severity,
    source: sanitizeNotificationText(input.source),
    message,
    details: safeDetails(input.details),
  };
  history = [record, ...history].slice(0, NOTIFICATION_HISTORY_LIMIT);
  persist();
  showToast(record.id);
  return record.id;
}

export function updateNotification(id: string, changes: Partial<NotificationInput>): void {
  const existing = history.find((record) => record.id === id);
  if (!existing) return;
  const now = new Date().toISOString();
  const updated: NotificationRecord = {
    ...existing,
    ...changes,
    message: changes.message === undefined ? existing.message : sanitizeNotificationText(changes.message),
    source: changes.source === undefined ? existing.source : sanitizeNotificationText(changes.source),
    details: changes.details === undefined ? existing.details : safeDetails(changes.details),
    updatedAt: now,
  };
  history = [updated, ...history.filter((record) => record.id !== id)]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  persist();
  showToast(id);
}

export function resolveNotification(id: string, changes: Partial<NotificationInput> = {}): void {
  updateNotification(id, { ...changes, active: false });
  const now = new Date().toISOString();
  history = history.map((record) => record.id === id ? { ...record, resolvedAt: now } : record);
  persist();
  notifySubscribers();
}

export function notificationsAtOrAbove(threshold: "info" | "warning" | "error") {
  return history.filter((record) => severityRank[record.severity] >= severityRank[threshold]);
}

export function buildNotificationExport(threshold: "info" | "warning" | "error"): string {
  const selected = notificationsAtOrAbove(threshold);
  return JSON.stringify({
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    severityThreshold: threshold,
    retainedRecordCount: history.length,
    notificationCount: selected.length,
    notifications: selected,
  }, null, 2);
}

export function clearNotificationHistory() {
  history = [];
  visibleToastIds = [];
  persist();
  notifySubscribers();
}
