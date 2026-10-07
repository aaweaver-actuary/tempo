import { API_URL } from "../const";
import { prefixSourceSchema, prefixComparisonSchema, type PrefixSource, type PrefixComparison } from "../domain/prefix-comparison";
import { z } from "zod";

export class PrefixPreviewError extends Error {
  constructor(public readonly code: string, message: string) { super(message); }
}
// Ordinary clicks hold the existing browser foreground lease. Wait on its
// observable status rather than outranking study, guessing a sleep, or retrying
// an evaluator failure. The server remains the final admission authority.
export async function waitForPrefixIdle(signal: AbortSignal): Promise<void> {
  const admissionSignal = AbortSignal.any([signal, AbortSignal.timeout(10_000)]);
  try {
    while (!admissionSignal.aborted) {
      const response = await fetch(`${API_URL}/api/system/foreground-active`, {
        signal: admissionSignal, cache: "no-store", headers: { "X-Tempo-Work-Class": "background" },
      });
      if (!response.ok) throw new PrefixPreviewError("service_error", "Cannot check study activity. Retry when the service is available.");
      const status = z.object({ active: z.boolean() }).safeParse(await response.json());
      if (!status.success) throw new PrefixPreviewError("service_error", "The service returned an unsupported study-activity response.");
      if (!status.data.active) return;
      await new Promise<void>((resolve, reject) => {
        const cancel = () => { clearTimeout(timer); reject(admissionSignal.reason); };
        const timer = setTimeout(() => { admissionSignal.removeEventListener("abort", cancel); resolve(); }, 250);
        admissionSignal.addEventListener("abort", cancel, { once: true });
        if (admissionSignal.aborted) cancel();
      });
    }
    throw admissionSignal.reason;
  } catch (cause) {
    if (!signal.aborted && admissionSignal.aborted)
      throw new PrefixPreviewError("evaluation_busy", "Study work remains active. Pause briefly and retry the preview.");
    throw cause;
  }
}

async function readDiagnostic<T>(url: string, schema: z.ZodType<T>, signal: AbortSignal, body?: unknown): Promise<T> {
  await waitForPrefixIdle(signal);
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(15_000)]), cache: "no-store",
    ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
  const payload: unknown = await response.json();
  if (!response.ok) {
    const failure = payload as { detail?: { code?: string; message?: string } | string };
    const detail = failure.detail;
    throw new PrefixPreviewError(typeof detail === "object" && detail !== null ? detail.code ?? "service_error" : "service_error",
      typeof detail === "string" ? detail : detail?.message ?? `Comparison unavailable (HTTP ${response.status}). Retry when the service is available.`);
  }
  const parsed = schema.safeParse(payload);
  if (!parsed.success) throw new PrefixPreviewError("unsupported_source", "The service returned an unsupported prefix-preview response. Refresh the source or retry when the service is available.");
  return parsed.data;
}
const endpoint = (repertoireId: string) => `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/prefix-evaluation`;
export async function readPrefixSource(repertoireId: string, signal: AbortSignal): Promise<PrefixSource> {
  const source = await readDiagnostic(`${endpoint(repertoireId)}/source`, prefixSourceSchema, signal);
  if (source.repertoire_id !== repertoireId || new Set(source.lines.map(line => line.id)).size !== source.lines.length)
    throw new PrefixPreviewError("unsupported_source", "The service returned an incompatible source snapshot. Refresh the source.");
  return source;
}
export async function comparePrefixDepth(repertoireId: string, source: PrefixSource, selectedIds: string[], depth: number, signal: AbortSignal): Promise<PrefixComparison> {
  const sortedIds = [...selectedIds].sort();
  const result = await readDiagnostic(`${endpoint(repertoireId)}/evaluate`, prefixComparisonSchema, signal, {
    snapshot_id: source.snapshot_id, selected_line_ids: sortedIds,
    candidate_depths: Object.fromEntries(sortedIds.map(id => [id, depth])),
  });
  if (result.repertoire_id !== repertoireId || result.snapshot_id !== source.snapshot_id || result.graph_generation !== source.graph_generation
    || JSON.stringify([...result.selected_line_ids].sort()) !== JSON.stringify(sortedIds)
    || result.selected_line_count !== sortedIds.length || result.line_depths.length !== sortedIds.length
    || new Set(result.line_depths.map(line => line.line_id)).size !== sortedIds.length
    || result.line_depths.some(line => !sortedIds.includes(line.line_id) || line.requested_depth !== depth
      || line.current_depth !== source.lines.find(saved => saved.id === line.line_id)?.saved_depth))
    throw new PrefixPreviewError("stale_snapshot", "The comparison does not match the selected snapshot. Refresh the source and select again.");
  return result;
}
