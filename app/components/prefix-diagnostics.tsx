import { useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { z } from "zod";
import { API_URL } from "../const";
import { backgroundReadWhenAdmitted } from "../lib/background-fetch";
import { Button } from "./buttons/BaseButton";
import { Notice } from "./task-tabs";
import { prefixDiagnosticsListSchema, prefixDiagnosticsDetailSchema,
  type PrefixDiagnosticsList, type PrefixDiagnosticsDetail, type DiagnosticPrefix } from "../domain/prefix-diagnostics";

function moveLabel(fen: string, expectedUci: string): string {
  return new Chess(fen).move(expectedUci)?.san ?? expectedUci;
}
async function readDiagnostics<T>(url: string, schema: z.ZodType<T>, signal: AbortSignal): Promise<T> {
  const response = await backgroundReadWhenAdmitted(url, AbortSignal.any([signal, AbortSignal.timeout(15_000)]));
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `Prefix difficulty unavailable (HTTP ${response.status}). Retry when study work settles.`);
  }
  return schema.parse(await response.json());
}
function outcomeLabel(outcome: PrefixDiagnosticsDetail["decisions"][number]["recent_outcomes"][number]) {
  const label = outcome.clean ? "Clean recall" : outcome.manual_failure ? "Manual failure"
    : outcome.first_response_correct === false ? "First-response failure"
    : outcome.first_response_correct === true ? "Assisted or unverified response" : "Reached without a first response";
  return `${outcome.study_day}: ${label}${outcome.corrected ? "; corrected" : ""}${outcome.revealed ? "; revealed" : ""}${outcome.assistance_before_response.length ? `; pre-response ${outcome.assistance_before_response.join(", ")}` : ""}`;
}

export function PrefixDiagnostics({ repertoireId, onBack }: { repertoireId: string; onBack: () => void }) {
  const [listing, setListing] = useState<PrefixDiagnosticsList | null>(null);
  const [detail, setDetail] = useState<PrefixDiagnosticsDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<{ generation: number; controller: AbortController | null }>({ generation: 0, controller: null });
  const baseUrl = `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/prefix-diagnostics`;

  function beginRequest() {
    request.current.controller?.abort();
    const controller = new AbortController();
    const generation = ++request.current.generation;
    request.current.controller = controller;
    return { controller, owns: () => !controller.signal.aborted && generation === request.current.generation };
  }
  async function loadList(after?: string) {
    const expectedGeneration = listing?.graph_generation;
    const { controller, owns } = beginRequest();
    setBusy(true); setError(""); setDetail(null); setListing(null);
    try {
      const parameters = new URLSearchParams();
      if (after && expectedGeneration !== undefined) {
        parameters.set("after_card_id", after); parameters.set("graph_generation", String(expectedGeneration));
      }
      const result = await readDiagnostics(`${baseUrl}?${parameters}`, prefixDiagnosticsListSchema, controller.signal);
      if (!owns()) return;
      if (result.repertoire_id !== repertoireId || (after && result.graph_generation !== expectedGeneration))
        throw new Error("The repertoire changed. Refresh Prefix difficulty before continuing.");
      setListing(result);
    } catch (cause) { if (owns()) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (owns()) setBusy(false); }
  }
  useEffect(() => {
    let disposed = false;
    const lifecycle = request.current;
    queueMicrotask(() => { if (!disposed) void loadList(); });
    return () => { disposed = true; lifecycle.controller?.abort(); ++lifecycle.generation; };
    // The repertoire owns this panel; navigation disposes every in-flight request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl]);

  async function inspect(prefix: DiagnosticPrefix) {
    if (!listing || !prefix.manifest) return;
    const expectedManifest = prefix.manifest;
    const expectedGeneration = listing.graph_generation;
    const { controller, owns } = beginRequest();
    setBusy(true); setError(""); setDetail(null);
    try {
      const parameters = new URLSearchParams({ manifest_id: expectedManifest.manifest_id, graph_generation: String(expectedGeneration) });
      const result = await readDiagnostics(`${baseUrl}/${encodeURIComponent(prefix.card_id)}?${parameters}`, prefixDiagnosticsDetailSchema, controller.signal);
      if (!owns()) return;
      if (JSON.stringify(result.manifest) !== JSON.stringify(expectedManifest) || result.graph_generation !== expectedGeneration)
        throw new Error("The prefix presentation changed. Refresh Prefix difficulty before continuing.");
      setDetail(result);
    } catch (cause) { if (owns()) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (owns()) setBusy(false); }
  }

  return <section className="library-page prefix-diagnostics">
    <Button onClick={onBack}>← Repertoires</Button>
    <h2>Prefix difficulty</h2>
    <p>Read-only decision evidence. Whole cards keep their review schedules.</p>
    <p>Coverage is unknown without unassisted responses, weak below three distinct unassisted response days, and strong at three or more. Coverage does not rate difficulty.</p>
    <Button disabled={busy} onClick={() => void loadList()}>Refresh prefixes</Button>
    {busy && <p role="status">Loading evidence…</p>}
    {error && <Notice error>{error}</Notice>}
    {listing && !listing.prefixes.length && <p>No current multi-decision opening prefixes.</p>}
    {listing?.prefixes.map(prefix => <div key={prefix.card_id}>
      {prefix.manifest ? <Button disabled={busy} onClick={() => void inspect(prefix)}>
        Inspect prefix: {prefix.presentation_san} ({prefix.manifest.trained_color})
      </Button> : <Notice error>{prefix.unavailable_reason ?? "Presentation unavailable. Refresh the repertoire."}</Notice>}
    </div>)}
    {listing?.next_card_id && <Button disabled={busy} onClick={() => void loadList(listing.next_card_id!)}>Next prefixes</Button>}
    {detail && <div>
      <h3>{listing?.prefixes.find(prefix => prefix.card_id === detail.manifest.card_id)?.presentation_san}</h3>
      <h3>{detail.manifest.trained_color === "white" ? "White" : "Black"} · presentation revision {detail.manifest.card_revision}</h3>
      <p>Latest {detail.window.attempt_count} of up to 100 attempts for this exact presentation. {detail.window.older_attempts_excluded ? "Older attempts are excluded." : "No older attempts excluded."} Counts below cover this window only.</p>
      <p>Only durable decision observations are shown. Legacy whole-card reviews are not decomposed; unsynced evidence may be absent.</p>
      {detail.decisions.map(decision => <article key={decision.decision_index} className="diagnostic-decision">
        <h3>Decision {decision.decision_index + 1}: {moveLabel(decision.fen, decision.expected_uci)}</h3>
        <p><strong>{decision.coverage === "unknown" ? "Unknown" : decision.coverage === "weak" ? "Weak evidence" : "Strong evidence"}</strong> · {decision.distinct_unassisted_response_days} distinct unassisted response days</p>
        {decision.reached_observations === 0 && <p>No observations for this decision.</p>}
        <p>Unassisted first responses: {decision.unassisted_first_responses} · failures: {decision.unassisted_first_response_failures}. Clean recall: {decision.clean_successes} across {decision.distinct_clean_days} distinct study days.</p>
        <p>All first responses: {decision.first_responses} · failures: {decision.first_response_failures}. Assistance before response: {decision.assistance_before_response}{Object.entries(decision.assistance_categories).length ? ` (${Object.entries(decision.assistance_categories).map(([category, count]) => `${category}: ${count}`).join(", ")})` : ""}.</p>
        <p>Corrections: {decision.corrections} · reveals: {decision.reveals} · manual failures: {decision.manual_failures}.</p>
        {decision.recent_outcomes.length > 0 && <details><summary>Recent reached observations ({decision.recent_outcomes.length})</summary>
          <ol>{decision.recent_outcomes.map(outcome => <li key={outcome.attempt_id}>{outcomeLabel(outcome)}</li>)}</ol>
        </details>}
      </article>)}
    </div>}
  </section>;
}
