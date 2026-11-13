import type * as z from "zod";
import { parseData as parseValidation, validRecords as validValidationRecords,
  type ValidationEffects } from "../domain/validation";
import type { ValidationReporter } from "../domain/validation";
export type { ValidationReportContext, ValidationReporter } from "../domain/validation";

export type DataDiagnostic = {
  source: string;
  recordId?: string;
  message: string;
  raw: unknown;
  recordedAt: string;
};

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

function collectorEffects(shouldPublishDiagnostics?: () => boolean, reporter?: ValidationReporter): ValidationEffects {
  return {
    shouldPublishDiagnostics,
    reportError: reporter,
    reportIssue: ({ source, raw, message, recordId }) => reportDataDiagnostic(source, raw, message, recordId),
  };
}

export function parseData<T>(
  schema: z.ZodType<T>, raw: unknown, source: string, endpoint?: string,
  debugSource = "validated-data", shouldPublishDiagnostics?: () => boolean, reporter?: ValidationReporter,
): T {
  return parseValidation(schema, raw, source, {
    endpoint, debugSource, effects: collectorEffects(shouldPublishDiagnostics, reporter),
  });
}

export function validRecords<T>(
  schema: z.ZodType<T>, records: readonly unknown[], source: string,
  shouldPublishDiagnostics?: () => boolean, reporter?: ValidationReporter,
): T[] {
  return validValidationRecords(schema, records, source, collectorEffects(shouldPublishDiagnostics, reporter));
}
