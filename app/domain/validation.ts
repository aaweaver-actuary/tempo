/** Pure parsing and validation facts. Effects are supplied explicitly by the caller. */
import type * as z from "zod";

export type ValidationIssue = {
  source: string;
  recordId?: string;
  message: string;
  raw: unknown;
};

export type ValidationReportContext = {
  kind: "data-validation";
  source: string;
  operation: "schema validation";
  endpoint?: string;
  notify: boolean;
};

export type ValidationReporter = (failure: Error, context: ValidationReportContext) => void;
export type ValidationEffects = {
  reportIssue?: (issue: ValidationIssue) => void;
  reportError?: ValidationReporter;
  shouldPublishDiagnostics?: () => boolean;
};

export function recordIdentity(raw: unknown): string | undefined {
  if (typeof raw !== "object" || raw === null) return undefined;
  for (const key of ["id", "PuzzleId", "queue_entry_id", "key"]) {
    const value = Reflect.get(raw, key);
    if (typeof value === "string" || typeof value === "number") return String(value);
  }
}

export function formatValidationIssues(issues: readonly { path: readonly PropertyKey[]; message: string }[]): string {
  return issues.map((issue) => `${issue.path.join(".") || "record"}: ${issue.message}`).join("; ");
}

/** Return the parsed value or throw the same Error offered to the optional reporter. */
export function parseData<T>(
  schema: z.ZodType<T>, raw: unknown, source: string,
  options: { endpoint?: string; debugSource?: string; effects?: ValidationEffects } = {},
): T {
  const result = schema.safeParse(raw);
  if (result.success) return result.data;
  const message = formatValidationIssues(result.error.issues);
  const effects = options.effects;
  if (effects?.shouldPublishDiagnostics?.() !== false)
    effects?.reportIssue?.({ source, raw, message, recordId: recordIdentity(raw) });
  const failure = new Error(`Invalid ${source} data: ${message}`);
  effects?.reportError?.(failure, {
    kind: "data-validation", source: options.debugSource ?? "validated-data",
    operation: "schema validation", endpoint: options.endpoint,
    notify: effects.shouldPublishDiagnostics?.() !== false,
  });
  throw failure;
}

/** Filter invalid records, reporting only through the supplied effects. */
export function validRecords<T>(
  schema: z.ZodType<T>, records: readonly unknown[], source: string,
  effects?: ValidationEffects,
): T[] {
  return records.flatMap((raw) => {
    try { return [parseData(schema, raw, source, { effects })]; }
    catch { return []; }
  });
}
