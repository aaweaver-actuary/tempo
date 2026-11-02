import * as z from "zod";
import { reportDebugError } from "./debug-reporting";
import {
  parseData as parseValidatedData,
  reportDataDiagnostic,
  validRecords as validatedRecords,
} from "./data-validation";

export type { DataDiagnostic } from "./data-validation";
export {
  dataDiagnostics,
  subscribeDataDiagnostics,
  clearDataDiagnostics,
  clearDataDiagnosticsForSources,
  reportDataDiagnostic,
} from "./data-validation";

export function parseData<T>(
  schema: z.ZodType<T>,
  raw: unknown,
  source: string,
  endpoint?: string,
  debugSource = "validated-data",
  shouldPublishDiagnostics?: () => boolean,
): T {
  return parseValidatedData(
    schema, raw, source, endpoint, debugSource, shouldPublishDiagnostics,
    reportDebugError,
  );
}

export function validRecords<T>(
  schema: z.ZodType<T>,
  records: readonly unknown[],
  source: string,
  shouldPublishDiagnostics?: () => boolean,
): T[] {
  return validatedRecords(
    schema, records, source, shouldPublishDiagnostics, reportDebugError,
  );
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
  options: { endpoint?: string; reportHttpFailure?: boolean } = {},
): Promise<T> {
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    if (!response.ok)
      throw new Error(`${source} failed (HTTP ${response.status})`);
    throw new Error(`Invalid ${source} response JSON`);
  }
  if (!response.ok) {
    const error = z
      .looseObject({ detail: z.string().optional() })
      .safeParse(raw);
    const failure = new Error(
      error.success && error.data.detail
        ? error.data.detail
        : `${source} failed (HTTP ${response.status})`,
    );
    if (options.reportHttpFailure !== false) reportDebugError(failure, {
      kind: "api",
      source: "validated-response",
      operation: "read JSON response",
      endpoint: options.endpoint || response.url || source,
      status: response.status,
    });
    throw failure;
  }
  return parseData(schema, raw, source, options.endpoint || response.url || undefined);
}
