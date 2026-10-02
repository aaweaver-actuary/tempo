export type NotificationSeverity = "info" | "success" | "warning" | "error";

export type NotificationRecord = {
  id: string;
  key?: string;
  occurredAt: string;
  updatedAt: string;
  occurrenceCount?: number;
  resolvedAt: string | null;
  clearedAt: string | null;
  active: boolean;
  severity: NotificationSeverity;
  source: string;
  message: string;
  details?: Record<string, string | number | boolean | string[]>;
};

export type NotificationInput = Pick<NotificationRecord, "severity" | "source" | "message"> &
  Partial<Pick<NotificationRecord, "key" | "active" | "details">>;

export type NotificationGroup = { key: string; record: NotificationRecord; occurrenceCount: number };
export type NotificationToast = { id: string; groupKey: string };

export const NOTIFICATION_HISTORY_LIMIT = 500;
const STORAGE_KEY = "tempo-notifications-v1";
const severityRank: Record<NotificationSeverity, number> = {
  info: 0, success: 0, warning: 1, error: 2,
};
const subscribers = new Set<() => void>();
let history: readonly NotificationRecord[] = [];
let visibleToasts: readonly NotificationToast[] = [];
let nextToastId = 1;
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

function opaqueKey(value: string): string {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (const character of value) {
    first = Math.imul(first ^ character.charCodeAt(0), 0x01000193);
    second = Math.imul(second ^ character.charCodeAt(0), 0x01000193);
  }
  return `${(first >>> 0).toString(16).padStart(8, "0")}${(second >>> 0).toString(16).padStart(8, "0")}`;
}

export function debugIncidentKey(signature: string): string {
  return `debug-incident:${opaqueKey(signature.split("\u0000").map(sanitizeNotificationText).join("\u0000"))}`;
}

function safeKey(key: string | undefined): string | undefined {
  if (!key) return key;
  if (key.startsWith("debug-incident:"))
    return /^debug-incident:[a-f0-9]{16}$/.test(key) ? key :
      debugIncidentKey(key.slice("debug-incident:".length));
  const sanitized = sanitizeNotificationText(key);
  return sanitized === key ? key : `notification-key:${opaqueKey(sanitized)}`;
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

function notificationContentChanged(previous: NotificationRecord, next: NotificationRecord): boolean {
  const meaningfulDetails = (details: NotificationRecord["details"]) => Object.entries(details ?? {})
    .filter(([name]) => name !== "debugRecordId" && name !== "stack")
    .sort(([firstName], [secondName]) => firstName.localeCompare(secondName));
  return previous.message !== next.message || previous.severity !== next.severity ||
    previous.source !== next.source ||
    JSON.stringify(meaningfulDetails(previous.details)) !== JSON.stringify(meaningfulDetails(next.details));
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
      .map((item) => ({ ...item,
        id: sanitizeNotificationText(item.id) === item.id ? item.id :
          `notification-${opaqueKey(item.id)}`,
        key: typeof item.key === "string" ? safeKey(item.key) : undefined,
        source: sanitizeNotificationText(item.source),
        clearedAt: typeof item.clearedAt === "string" && Number.isFinite(Date.parse(item.clearedAt))
          ? new Date(item.clearedAt).toISOString() : null,
        active: false, occurrenceCount: item.occurrenceCount ?? 1,
        message: sanitizeNotificationText(item.message), details: safeDetails(item.details) }));
    history = [...history, ...restored.filter((item) => !history.some((current) => current.id === item.id))]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, NOTIFICATION_HISTORY_LIMIT);
    notifySubscribers();
    persist();
  } catch {
    // A damaged history must not prevent the app from opening.
  }
}

export function subscribeNotifications(subscriber: () => void) {
  subscribers.add(subscriber);
  return () => subscribers.delete(subscriber);
}

export function notifications() { return history; }
export function notificationToasts() { return visibleToasts; }
export function notificationToastIds() {
  const groups = groupNotifications(history);
  return visibleToasts.flatMap((toast) => {
    const group = groups.find((candidate) => candidate.key === toast.groupKey);
    return group ? [group.record.id] : [];
  });
}

export function notificationNeedsAttention(record: NotificationRecord): boolean {
  return !record.clearedAt && !record.resolvedAt &&
    (record.severity === "warning" || record.severity === "error");
}

function notificationGroupKey(record: NotificationRecord): string {
  const details = Object.entries(record.details ?? {}).sort(([firstName], [secondName]) => firstName.localeCompare(secondName));
  return JSON.stringify([record.source, record.severity, record.message, details,
    Boolean(record.resolvedAt), Boolean(record.clearedAt)]);
}

export function groupNotifications(records: readonly NotificationRecord[]): NotificationGroup[] {
  const groups = new Map<string, NotificationGroup>();
  for (const record of [...records].sort((first, second) => second.updatedAt.localeCompare(first.updatedAt))) {
    const groupKey = notificationGroupKey(record);
    const existingGroup = groups.get(groupKey);
    if (existingGroup) existingGroup.occurrenceCount += record.occurrenceCount ?? 1;
    else groups.set(groupKey, { key: groupKey, record, occurrenceCount: record.occurrenceCount ?? 1 });
  }
  return [...groups.values()];
}

function finishNotificationChange(previousHistory: readonly NotificationRecord[], repeatedRecordId?: string) {
  const attentionGroups = groupNotifications(history).filter((group) => notificationNeedsAttention(group.record));
  const previousGroupKeys = new Set(previousHistory.filter(notificationNeedsAttention).map(notificationGroupKey));
  const previousObservation = previousHistory.find((record) => record.id === repeatedRecordId);
  const updatedObservation = history.find((record) => record.id === repeatedRecordId);
  const updatedGroupKey = updatedObservation && notificationGroupKey(updatedObservation);
  const retainedToasts = visibleToasts.flatMap((toast) => {
    // A keyed repeat can refresh diagnostic details without restarting its popup.
    const groupKey = previousObservation && toast.groupKey === notificationGroupKey(previousObservation)
      ? updatedGroupKey : toast.groupKey;
    return groupKey && attentionGroups.some((group) => group.key === groupKey)
      ? [{ ...toast, groupKey }] : [];
  }).filter((toast, index, toasts) => toasts.findIndex((candidate) => candidate.groupKey === toast.groupKey) === index);
  const newToasts = attentionGroups.filter((group) => !previousGroupKeys.has(group.key) &&
    group.key !== updatedGroupKey && !retainedToasts.some((toast) => toast.groupKey === group.key))
    .map((group) => ({ id: `notification-toast-${nextToastId++}`, groupKey: group.key }));
  visibleToasts = [...newToasts, ...retainedToasts].slice(0, 3);
  persist();
  notifySubscribers();
}

export function hideNotificationToast(id: string) {
  const groups = groupNotifications(history);
  const matchingToast = visibleToasts.find((toast) => toast.id === id ||
    groups.some((group) => group.key === toast.groupKey && group.record.id === id));
  if (!matchingToast) return;
  visibleToasts = visibleToasts.filter((toast) => toast.id !== matchingToast.id);
  notifySubscribers();
}

export function publishNotification(input: NotificationInput): string {
  hydrateNotifications();
  const message = sanitizeNotificationText(input.message);
  const key = safeKey(input.key);
  const existing = key && history.find((record) => record.key === key && !record.resolvedAt);
  if (existing && existing.message === message && existing.severity === input.severity) {
    const previousHistory = history;
    const observed = { ...existing, occurrenceCount: (existing.occurrenceCount ?? 1) + 1,
      source: sanitizeNotificationText(input.source),
      updatedAt: new Date().toISOString(), details: safeDetails(input.details) ?? existing.details };
    if (notificationContentChanged(existing, observed)) observed.clearedAt = null;
    history = [observed, ...history.filter((record) => record.id !== existing.id)]
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    finishNotificationChange(previousHistory, existing.clearedAt && !observed.clearedAt ? undefined : existing.id);
    return existing.id;
  }
  if (existing) {
    updateNotification(existing.id, input);
    return existing.id;
  }
  const now = new Date().toISOString();
  const previousHistory = history;
  const record: NotificationRecord = {
    id: typeof crypto !== "undefined" && "randomUUID" in crypto
      ? `notification-${crypto.randomUUID()}` : `notification-${Date.now()}-${nextId++}`,
    key,
    occurredAt: now,
    updatedAt: now,
    occurrenceCount: 1,
    resolvedAt: null,
    clearedAt: null,
    active: input.active ?? false,
    severity: input.severity,
    source: sanitizeNotificationText(input.source),
    message,
    details: safeDetails(input.details),
  };
  history = [record, ...history].slice(0, NOTIFICATION_HISTORY_LIMIT);
  finishNotificationChange(previousHistory);
  return record.id;
}

function changeNotification(id: string, changes: Partial<NotificationInput>, resolvedAt?: string): void {
  const existing = history.find((record) => record.id === id);
  if (!existing) return;
  const now = new Date().toISOString();
  const previousHistory = history;
  const updated: NotificationRecord = {
    ...existing,
    ...changes,
    key: changes.key === undefined ? existing.key : safeKey(changes.key),
    message: changes.message === undefined ? existing.message : sanitizeNotificationText(changes.message),
    source: changes.source === undefined ? existing.source : sanitizeNotificationText(changes.source),
    details: changes.details === undefined ? existing.details : safeDetails(changes.details),
    updatedAt: now,
    resolvedAt: resolvedAt ?? existing.resolvedAt,
  };
  if (notificationContentChanged(existing, updated)) updated.clearedAt = null;
  history = [updated, ...history.filter((record) => record.id !== id)]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const repeated = !existing.resolvedAt && !updated.resolvedAt && existing.source === updated.source &&
    existing.severity === updated.severity && existing.message === updated.message &&
    Boolean(existing.clearedAt) === Boolean(updated.clearedAt);
  finishNotificationChange(previousHistory, repeated ? id : undefined);
}

export function updateNotification(id: string, changes: Partial<NotificationInput>): void {
  changeNotification(id, changes);
}

export function resolveNotification(id: string, changes: Partial<NotificationInput> = {}): void {
  changeNotification(id, { ...changes, active: false }, new Date().toISOString());
}

export function notificationsAtOrAbove(threshold: "info" | "warning" | "error") {
  return history.filter((record) => severityRank[record.severity] >= severityRank[threshold]);
}

function clearSelectedNotifications(notificationIds: ReadonlySet<string>) {
  const clearedAt = new Date().toISOString();
  let changed = false;
  const clearedHistory = history.map((record) => {
    if (!notificationIds.has(record.id) || record.clearedAt) return record;
    changed = true;
    return { ...record, clearedAt };
  });
  if (!changed) return;
  const previousHistory = history;
  history = clearedHistory;
  finishNotificationChange(previousHistory);
}

export function clearNotification(notificationId: string): void {
  hydrateNotifications();
  clearSelectedNotifications(new Set([notificationId]));
}

export function clearNotificationGroup(groupKey: string): void {
  hydrateNotifications();
  clearSelectedNotifications(new Set(history.filter((record) => notificationGroupKey(record) === groupKey)
    .map((record) => record.id)));
}

export function clearAllNotifications(): void {
  hydrateNotifications();
  clearSelectedNotifications(new Set(history.map((record) => record.id)));
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
  visibleToasts = [];
  persist();
  notifySubscribers();
}
