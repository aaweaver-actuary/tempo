import type * as z from "zod";

export type DataDiagnostic = {
  source: string;
  recordId?: string;
  message: string;
  raw: unknown;
  recordedAt: string;
};

export type ValidationReportContext = {
  kind: "data-validation";
  source: string;
  operation: "schema validation";
  endpoint?: string;
  notify: boolean;
};

export type ValidationReporter = (
  failure: Error,
  context: ValidationReportContext,
) => void;

let diagnostics: readonly DataDiagnostic[] = [];
const listeners = new Set<() => void>();
function notifyDiagnostics() {
  queueMicrotask(() => listeners.forEach((listener) => listener()));
}
export function dataDiagnostics(): readonly DataDiagnostic[] {
  return diagnostics;
}
export function subscribeDataDiagnostics(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function clearDataDiagnostics(): void {
  diagnostics = [];
  notifyDiagnostics();
}

export function clearDataDiagnosticsForSources(sources: readonly string[]): void {
  const sourceSet = new Set(sources);
  const remainingDiagnostics = diagnostics.filter(
    (diagnostic) => !sourceSet.has(diagnostic.source),
  );
  if (remainingDiagnostics.length === diagnostics.length) return;
  diagnostics = remainingDiagnostics;
  notifyDiagnostics();
}

export function reportDataDiagnostic(
  source: string,
  raw: unknown,
  message: string,
  recordId?: string,
): void {
  if (diagnostics.some((issue) => issue.source === source && issue.raw === raw))
    return;
  if (
    diagnostics.some(
      (issue) =>
        issue.source === source &&
        issue.recordId === recordId &&
        issue.message === message,
    )
  )
    return;
  diagnostics = [
    ...diagnostics.slice(-99),
    { source, raw, message, recordId, recordedAt: new Date().toISOString() },
  ];
  notifyDiagnostics();
}

function recordIdentity(raw: unknown): string | undefined {
  if (typeof raw !== "object" || raw === null) return undefined;
  for (const key of ["id", "PuzzleId", "queue_entry_id", "key"]) {
    const value = Reflect.get(raw, key);
    if (typeof value === "string" || typeof value === "number")
      return String(value);
  }
}

export function parseData<T>(
  schema: z.ZodType<T>,
  raw: unknown,
  source: string,
  endpoint?: string,
  debugSource = "validated-data",
  shouldPublishDiagnostics?: () => boolean,
  reporter?: ValidationReporter,
): T {
  const result = schema.safeParse(raw);
  if (result.success) return result.data;
  const message = result.error.issues
    .map((issue) => `${issue.path.join(".") || "record"}: ${issue.message}`)
    .join("; ");
  if (shouldPublishDiagnostics?.() !== false)
    reportDataDiagnostic(source, raw, message, recordIdentity(raw));
  const failure = new Error(`Invalid ${source} data: ${message}`);
  reporter?.(failure, {
    kind: "data-validation",
    source: debugSource,
    operation: "schema validation",
    endpoint,
    notify: shouldPublishDiagnostics?.() !== false,
  });
  throw failure;
}

export function validRecords<T>(
  schema: z.ZodType<T>,
  records: readonly unknown[],
  source: string,
  shouldPublishDiagnostics?: () => boolean,
  reporter?: ValidationReporter,
): T[] {
  return records.flatMap((raw) => {
    try {
      return [parseData(
        schema, raw, source, undefined, undefined, shouldPublishDiagnostics,
        reporter,
      )];
    } catch {
      return [];
    }
  });
}
