import { useBoardShellStore } from "../state/board-shell-store";
import { useTrainingStore } from "../state/training-store";
import { dataDiagnostics } from "./validated-data";
import { usesLocalApi } from "../utils/local";
import { serviceStatusSnapshot } from "./service-status";

export type DebugErrorKind =
  | "uncaught-exception"
  | "unhandled-rejection"
  | "render"
  | "api"
  | "data-validation"
  | "ui";

export type DebugErrorContext = {
  kind?: DebugErrorKind;
  source?: string;
  operation?: string;
  endpoint?: string;
  method?: string;
  status?: number;
  retryable?: boolean;
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
  };
};

const MAX_ERROR_RECORDS = 20;
const MAX_TEXT_LENGTH = 2_000;
const listeners = new Set<() => void>();
let records: readonly DebugErrorRecord[] = [];
let activeWorkspace = "unknown";
let nextErrorId = 1;
let globalCleanup: (() => void) | undefined;

function notify() {
  listeners.forEach((listener) => listener());
}

function sanitizeText(value: string): string {
  return value
    .replace(/https?:\/\/[^\s)]+/gi, (url) => {
      try {
        return new URL(url).pathname;
      } catch {
        return "[redacted-url]";
      }
    })
    .replace(/[?&][A-Za-z0-9_.-]+=[^\s&]+/g, (parameter) => `${parameter.slice(0, parameter.indexOf("=") + 1)}[redacted]`)
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[redacted-email]")
    .slice(0, MAX_TEXT_LENGTH);
}

function errorDetails(failure: unknown): {
  name: string;
  message: string;
  stack?: string;
} {
  if (failure instanceof Error) {
    return {
      name: failure.name || "Error",
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
  activeWorkspace = workspace;
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
  notify();
}

export function reportDebugError(
  failure: unknown,
  context: DebugErrorContext = {},
): DebugErrorRecord {
  const details = errorDetails(failure);
  const normalizedContext = {
    source: context.source ?? "frontend",
    operation: context.operation ? sanitizeText(context.operation) : undefined,
    endpointPath: endpointPath(context.endpoint),
    method: context.method,
    status: context.status,
    retryable: context.retryable,
  };
  const signature = [
    context.kind ?? "ui",
    normalizedContext.endpointPath ?? "",
    details.name,
    details.message,
  ].join("\u0000");
  const previous = records[records.length - 1];
  if (
    previous &&
    [
      previous.kind,
      previous.context.endpointPath ?? "",
      previous.name,
      previous.message,
    ].join("\u0000") === signature &&
    Date.now() - Date.parse(previous.occurredAt) < 1_000
  )
    return previous;

  const record: DebugErrorRecord = {
    id: `debug-${nextErrorId++}`,
    occurredAt: new Date().toISOString(),
    kind: context.kind ?? "ui",
    name: details.name,
    message: details.message,
    stack: details.stack,
    context: normalizedContext,
  };
  records = [...records.slice(-(MAX_ERROR_RECORDS - 1)), record];
  notify();
  return record;
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
    })),
    environment: environmentSnapshot(),
    workspace: {
      activeView: activeWorkspace,
      board: {
        owner: board.owner,
        fen: board.fen,
        orientation: board.orientation,
        interactionMode: board.interactionMode,
        unavailable: board.unavailable,
        positionRevision: board.positionRevision,
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
    omitted: [
      "raw response payloads",
      "PGN and repertoire lines",
      "usernames, notes, and full card records",
      "local/session-storage values",
      "URL query parameters and secrets",
    ],
  };
  return JSON.stringify(payload, null, 2);
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
  const onError = (event: ErrorEvent) => {
    reportDebugError(event.error ?? event.message, {
      kind: "uncaught-exception",
      source: "window.error",
    });
  };
  const onUnhandledRejection = (event: PromiseRejectionEvent) => {
    reportDebugError(event.reason, {
      kind: "unhandled-rejection",
      source: "window.unhandledrejection",
    });
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onUnhandledRejection);
  globalCleanup = () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onUnhandledRejection);
    globalCleanup = undefined;
  };
  return globalCleanup;
}
