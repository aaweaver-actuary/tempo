import { tempoDragDiagnostics } from "./performance";
import { boardDiagnostics } from "./board-diagnostics";
import { useBoardShellStore } from "../state/board-shell-store";
import { useTrainingStore } from "../state/training-store";
import { dataDiagnostics } from "./validated-data";
import { usesLocalApi } from "../utils/local";
import { backgroundDiagnosticsSnapshot, serviceStatusSnapshot } from "./service-status";
import { offlineShellVersion } from "./offline-shell";
import { debugIncidentKey, notifications, publishNotification,
  resolveNotification, sanitizeNotificationText } from "./notifications";

export type DebugErrorKind =
  | "uncaught-exception"
  | "unhandled-rejection"
  | "render"
  | "api"
  | "data-validation"
  | "ui";

export type DebugErrorContext = {
  notify?: boolean;
  kind?: DebugErrorKind;
  source?: string;
  operation?: string;
  endpoint?: string;
  method?: string;
  status?: number;
  retryable?: boolean;
  cardId?: string;
  queueEntryId?: number;
  attemptId?: string;
  code?: string;
  classification?: "conflict" | "pending" | "transient" | "failed" | "storage";
  script?: string;
  line?: number;
  column?: number;
};

export type DebugErrorRecord = {
  id: string;
  occurredAt: string;
  kind: DebugErrorKind;
  name: string;
  message: string;
  stack?: string;
  context: {
    source: string;
    operation?: string;
    endpointPath?: string;
    method?: string;
    status?: number;
    retryable?: boolean;
    cardId?: string;
    queueEntryId?: number;
    attemptId?: string;
    code?: string;
    classification?: "conflict" | "pending" | "transient" | "failed" | "storage";
    scriptPath?: string;
    line?: number;
    column?: number;
  };
};

const MAX_ERROR_RECORDS = 20;
const MAX_TEXT_LENGTH = 2_000;
const listeners = new Set<() => void>();
let records: readonly DebugErrorRecord[] = [];
let activeWorkspace = "unknown";
let nextErrorId = 1;
let alreadyReported = new WeakMap<Error, DebugErrorRecord>();
let globalCleanup: (() => void) | undefined;

function notify() {
  listeners.forEach((listener) => listener());
}

function sanitizeText(value: string): string {
  return sanitizeNotificationText(value).slice(0, MAX_TEXT_LENGTH);
}

function errorDetails(failure: unknown): {
  name: string;
  message: string;
  stack?: string;
} {
  if (failure instanceof Error) {
    return {
      name: sanitizeText(failure.name || "Error"),
      message: sanitizeText(failure.message || "Unknown error"),
      stack: failure.stack ? sanitizeText(failure.stack) : undefined,
    };
  }
  if (typeof failure === "string")
    return { name: "Error", message: sanitizeText(failure) };
  return {
    name: "UnknownError",
    message: sanitizeText(String(failure ?? "Unknown error")),
  };
}

function endpointPath(endpoint: string | undefined): string | undefined {
  if (!endpoint) return undefined;
  if (endpoint.startsWith("storage:")) return sanitizeText(endpoint);
  try {
    return new URL(endpoint, typeof document === "undefined" ? "http://tempo.local" : document.baseURI).pathname;
  } catch {
    return sanitizeText(endpoint.split("?")[0].split("#")[0]);
  }
}

export function setActiveDebugWorkspace(workspace: string) {
  activeWorkspace = sanitizeText(workspace);
}

export function subscribeDebugErrors(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function debugErrors() {
  return records;
}

export function clearDebugErrors() {
  records = [];
  alreadyReported = new WeakMap();
  notify();
}

export function reportDebugError(
  failure: unknown,
  context: DebugErrorContext = {},
): DebugErrorRecord {
  if (failure instanceof Error) {
    const priorReport = alreadyReported.get(failure);
    if (priorReport) return priorReport;
  }
  const details = errorDetails(failure);
  const normalizedContext = {
    source: sanitizeText(context.source ?? "frontend"),
    operation: context.operation ? sanitizeText(context.operation) : undefined,
    endpointPath: endpointPath(context.endpoint),
    method: context.method ? sanitizeText(context.method) : undefined,
    status: context.status,
    retryable: context.retryable,
    cardId: context.cardId ? sanitizeText(context.cardId) : undefined,
    queueEntryId: context.queueEntryId,
    attemptId: context.attemptId ? sanitizeText(context.attemptId) : undefined,
    code: context.code ? sanitizeText(context.code) : undefined,
    classification: context.classification,
    scriptPath: endpointPath(context.script),
    line: context.line,
    column: context.column,
  };
  const signature = [
    context.kind ?? "ui",
    normalizedContext.source,
    normalizedContext.endpointPath ?? "",
    details.name,
    details.message,
  ].join("\u0000");
  const previous = records[records.length - 1];
  if (
    previous &&
    [
      previous.kind,
      previous.context.source,
      previous.context.endpointPath ?? "",
      previous.name,
      previous.message,
    ].join("\u0000") === signature &&
    Date.now() - Date.parse(previous.occurredAt) < 1_000
  )
    return previous;

  const record: DebugErrorRecord = {
    id: `debug-${typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID() : `${Date.now()}-${nextErrorId++}-${Math.random()}`}`,
    occurredAt: new Date().toISOString(),
    kind: context.kind ?? "ui",
    name: details.name,
    message: details.message,
    stack: details.stack,
    context: normalizedContext,
  };
  records = [...records.slice(-(MAX_ERROR_RECORDS - 1)), record];
  if (failure instanceof Error) alreadyReported.set(failure, record);
  notify();
  if (context.notify !== false && normalizedContext.source !== "browser-extension") publishNotification({
    severity: "error", source: normalizedContext.source,
    message: record.message, key: debugIncidentKey(signature),
    details: {
      debugRecordId: record.id,
      kind: record.kind,
      ...(normalizedContext.operation ? { operation: normalizedContext.operation } : {}),
      ...(normalizedContext.endpointPath ? { endpointPath: normalizedContext.endpointPath } : {}),
      ...(normalizedContext.method ? { method: normalizedContext.method } : {}),
      ...(normalizedContext.status ? { status: normalizedContext.status } : {}),
      ...(record.stack ? { stack: record.stack } : {}),
    },
  });
  return record;
}

export function resolveValidationIncidentsForEndpoint(endpoint: string): void {
  const resolvedPath = endpointPath(endpoint);
  for (const record of notifications()) {
    if (record.resolvedAt || record.source !== "validated-data" ||
        record.details?.kind !== "data-validation" ||
        record.details.endpointPath !== resolvedPath) continue;
    resolveNotification(record.id);
  }
}

export function resolveApiIncidentsForEndpoint(endpoint: string, source: string): void {
  const resolvedPath = endpointPath(endpoint);
  for (const record of notifications()) {
    if (record.resolvedAt || record.source !== source ||
        record.details?.kind !== "api" ||
        record.details.endpointPath !== resolvedPath) continue;
    resolveNotification(record.id);
  }
}

function diagnosticSummary() {
  const groups = new Map<string, { source: string; message: string; count: number }>();
  for (const diagnostic of dataDiagnostics()) {
    const source = sanitizeText(diagnostic.source);
    const message = sanitizeText(diagnostic.message);
    const key = `${source}\u0000${message}`;
    const current = groups.get(key) ?? { source, message, count: 0 };
    current.count += 1;
    groups.set(key, current);
  }
  return Array.from(groups.values());
}

function environmentSnapshot() {
  if (typeof window === "undefined")
    return { runtime: "server", apiMode: "unknown" };
  return {
    runtime: usesLocalApi() ? "local-api" : "demo-static",
    apiMode: usesLocalApi() ? "local" : "demo",
    path: window.location.pathname,
    userAgent: sanitizeText(navigator.userAgent),
    language: navigator.language,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    online: navigator.onLine,
    serviceWorkerPath: endpointPath(navigator.serviceWorker?.controller?.scriptURL),
    offlineShellVersion: offlineShellVersion(),
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      devicePixelRatio: window.devicePixelRatio,
    },
  };
}

export function buildDebugBundle(recordId?: string): string {
  const selected = recordId
    ? records.find((record) => record.id === recordId)
    : records[records.length - 1];
  const training = useTrainingStore.getState();
  const board = useBoardShellStore.getState().board;
  const payload = {
    schemaVersion: 1,
    generatedAt: new Date().toISOString(),
    error: selected ?? null,
    recentErrors: records.slice(-10).map((record) => ({
      id: record.id,
      occurredAt: record.occurredAt,
      kind: record.kind,
      name: record.name,
      message: record.message,
      source: record.context.source,
      endpointPath: record.context.endpointPath,
      cardId: record.context.cardId,
      queueEntryId: record.context.queueEntryId,
      attemptId: record.context.attemptId,
      code: record.context.code,
      status: record.context.status,
      retryable: record.context.retryable,
      classification: record.context.classification,
      scriptPath: record.context.scriptPath,
      line: record.context.line,
      column: record.context.column,
    })),
    environment: environmentSnapshot(),
    dragDiagnostics: tempoDragDiagnostics(),
    workspace: {
      activeView: activeWorkspace,
      board: {
        owner: board.owner,
        fen: board.fen,
        orientation: board.orientation,
        interactionMode: board.interactionMode,
        unavailable: board.unavailable,
        positionRevision: board.positionRevision,
        counters: boardDiagnostics(),
      },
      training: {
        activeCardIndex: training.activeCardIndex,
        currentFen: training.currentFenString,
        step: training.step,
        feedback: training.feedback,
        cardsLeft: training.cardsLeft,
        reviewed: training.reviewed,
        attemptPhase: training.attempt.phase,
        attemptGeneration: training.attempt.generation,
        isAttemptFailed: training.isAttemptFailed,
        isDatabaseQueueActive: training.isDatabaseQueueActive,
      },
    },
    dataDiagnostics: diagnosticSummary(),
    serviceStatus: serviceStatusSnapshot(),
    backgroundDiagnostics: backgroundDiagnosticsSnapshot(),
    omitted: [
      "raw response payloads",
      "PGN and repertoire lines",
      "usernames, notes, and full card records",
      "local/session-storage values",
      "URL query parameters and secrets",
    ],
  };
  return JSON.stringify(payload, (_key, value: unknown) =>
    typeof value === "string" ? sanitizeText(value) : value, 2);
}

export async function copyDebugBundle(recordId?: string): Promise<boolean> {
  const text = buildDebugBundle(recordId);
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the selection-based copy for older or restricted browsers.
    }
  }
  if (typeof document === "undefined") return false;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  textarea.remove();
  return copied;
}

export function installGlobalDebugErrorHandlers() {
  if (globalCleanup || typeof window === "undefined") return globalCleanup;
  const onError = (event: Event) => {
    if (!(event instanceof ErrorEvent)) {
      const resource = event.target;
      if (resource instanceof HTMLScriptElement || resource instanceof HTMLLinkElement) {
        reportDebugError(new Error("A Tempo script or stylesheet failed to load. Reopen Tempo while connected."), {
          kind: "uncaught-exception", source: "resource.error",
          script: resource instanceof HTMLScriptElement ? resource.src : resource.href,
        });
      }
      return;
    }
    reportDebugError(event.error ?? event.message, {
      kind: "uncaught-exception",
      source: "window.error",
      script: event.filename,
      line: event.lineno || undefined,
      column: event.colno || undefined,
    });
  };
  const onUnhandledRejection = (event: PromiseRejectionEvent) => {
    const rejectionMessage = event.reason instanceof Error ? event.reason.message : String(event.reason ?? "");
    const isSafariExtensionFailure = rejectionMessage.includes("error in the background page") &&
      rejectionMessage.includes("window.fixinatorInputs.has");
    reportDebugError(event.reason, {
      kind: "unhandled-rejection",
      source: isSafariExtensionFailure ? "browser-extension" : "window.unhandledrejection",
    });
  };
  window.addEventListener("error", onError, true);
  window.addEventListener("unhandledrejection", onUnhandledRejection);
  globalCleanup = () => {
    window.removeEventListener("error", onError, true);
    window.removeEventListener("unhandledrejection", onUnhandledRejection);
    globalCleanup = undefined;
  };
  return globalCleanup;
}
