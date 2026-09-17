import * as z from "zod";

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
export function dataDiagnostics() {
  return diagnostics;
}
export function subscribeDataDiagnostics(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function clearDataDiagnostics() {
  diagnostics = [];
  notifyDiagnostics();
}

export function reportDataDiagnostic(
  source: string,
  raw: unknown,
  message: string,
  recordId?: string,
) {
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
): T {
  const result = schema.safeParse(raw);
  if (result.success) return result.data;
  const message = result.error.issues
    .map((issue) => `${issue.path.join(".") || "record"}: ${issue.message}`)
    .join("; ");
  reportDataDiagnostic(source, raw, message, recordIdentity(raw));
  throw new Error(`Invalid ${source} data: ${message}`);
}

export function validRecords<T>(
  schema: z.ZodType<T>,
  records: readonly unknown[],
  source: string,
): T[] {
  return records.flatMap((raw) => {
    try {
      return [parseData(schema, raw, source)];
    } catch {
      return [];
    }
  });
}

export function readStoredValue<T>(
  storage: Storage,
  key: string,
  schema: z.ZodType<T>,
): T | undefined {
  const raw = storage.getItem(key);
  if (raw === null) return undefined;
  try {
    return parseData(schema, JSON.parse(raw), `storage:${key}`);
  } catch (error) {
    if (error instanceof SyntaxError)
      reportDataDiagnostic(`storage:${key}`, raw, "Malformed JSON");
    return undefined;
  }
}

export async function readJsonResponse<T>(
  response: Response,
  schema: z.ZodType<T>,
  source: string,
): Promise<T> {
  const raw: unknown = await response.json();
  if (!response.ok) {
    const error = z
      .looseObject({ detail: z.string().optional() })
      .safeParse(raw);
    throw new Error(
      error.success && error.data.detail
        ? error.data.detail
        : `${source} failed (HTTP ${response.status})`,
    );
  }
  return parseData(schema, raw, source);
}
