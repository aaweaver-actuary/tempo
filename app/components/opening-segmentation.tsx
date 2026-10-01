import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Button } from "./buttons/BaseButton";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import { Notice } from "./task-tabs";
import { API_URL } from "../const";
import { confirmOperationResponse } from "../lib/operation-status";
import { matchesSegmentationSnapshot, segmentationDetailSchema, segmentationListSchema, type SegmentationDetail } from "../domain/opening-segmentation";

async function readAdvice<T>(url: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal: signal ?? AbortSignal.timeout(15_000) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail ?? `Recommended segmentation unavailable (HTTP ${response.status}). Retry when the service is available.`);
  }
  return schema.parse(await response.json());
}
const roleLabels = { shared_trunk: "Shared opening", branch: "Branch", bridge: "Incoming route", shared_continuation: "Shared continuation" };

export function OpeningSegmentation({ repertoireId, theme, pieceSet, initiallyOpened = false }: {
  repertoireId: string; theme: BoardTheme; pieceSet: PieceSet; initiallyOpened?: boolean;
}) {
  const [opened, setOpened] = useState(initiallyOpened);
  const [revision, setRevision] = useState(0);
  const [advice, setAdvice] = useState<z.infer<typeof segmentationListSchema> | null>(null);
  const [detail, setDetail] = useState<SegmentationDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const commandGeneration = useRef(0);
  const currentAdvice = useRef<typeof advice>(null);
  const currentDetail = useRef<SegmentationDetail | null>(null);
  const pendingCommand = useRef<{ payload: string; key: string } | null>(null);
  const baseUrl = `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/segmentation`;
  useEffect(() => {
    const generation = ++listGeneration.current;
    ++detailGeneration.current;
    ++commandGeneration.current;
    currentAdvice.current = null; currentDetail.current = null;
    queueMicrotask(() => {
      if (generation !== listGeneration.current) return;
      setAdvice(null); setDetail(null); setBusy(false); setError("");
    });
    if (!opened) return;
    const controller = new AbortController();

    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    async function load() {
      try {
        const result = await readAdvice(baseUrl, segmentationListSchema, AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]));
        if (controller.signal.aborted || generation !== listGeneration.current) return;
        currentAdvice.current = result;
        if (currentDetail.current && !matchesSegmentationSnapshot(currentDetail.current, result, repertoireId, currentDetail.current.recommendation.id)) {
          ++detailGeneration.current; ++commandGeneration.current;
          currentDetail.current = null; setDetail(null); setBusy(false);
        }
        setAdvice(result);
        setError(result.error ?? "");
        if (result.state === "building" && Date.now() - started < 60_000) timer = setTimeout(() => void load(), 2_000);
      } catch (cause) {
        if (!controller.signal.aborted && generation === listGeneration.current) setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
    void load();
    return () => {
      ++listGeneration.current; ++detailGeneration.current; ++commandGeneration.current;
      currentAdvice.current = null; currentDetail.current = null;
      controller.abort(); if (timer) clearTimeout(timer);
    };
  }, [opened, revision, baseUrl, repertoireId]);

  async function preview(id: string, page?: "segments" | "routes") {
    const expected = currentAdvice.current?.recommendations.find(item => item.id === id);
    const displayed = currentDetail.current;
    if (!expected || currentAdvice.current?.state !== "ready") return;
    if (page && (!displayed || !matchesSegmentationSnapshot(displayed, currentAdvice.current, repertoireId, id))) return;
    const generation = ++detailGeneration.current;
    const listRequest = listGeneration.current;
    const ownsRequest = () => generation === detailGeneration.current && listRequest === listGeneration.current;
    setBusy(true); setError("");
    try {
      const parameters = new URLSearchParams({ snapshot_id: expected.snapshot_id });
      if (page === "segments") parameters.set("after_segment", displayed?.next_segment ?? "");
      if (page === "routes") parameters.set("after_route", displayed?.next_route ?? "");
      const result = await readAdvice(`${baseUrl}/${encodeURIComponent(id)}?${parameters}`, segmentationDetailSchema);
      if (!ownsRequest()) return;
      if (!matchesSegmentationSnapshot(result, currentAdvice.current, repertoireId, id)
        || (page && (!displayed || result.snapshot_id !== displayed.snapshot_id || currentDetail.current !== displayed))) {
        currentDetail.current = null; setDetail(null);
        throw new Error("This preview snapshot changed. Refresh recommendations before continuing.");
      }
      // Preserve the displayed snapshot's metadata; append only its matching page.
      const next = page && displayed ? { ...displayed,
        segments: page === "segments" ? [...displayed.segments, ...result.segments] : displayed.segments,
        next_segment: page === "segments" ? result.next_segment : displayed.next_segment,
        routes: page === "routes" ? [...displayed.routes, ...result.routes] : displayed.routes,
        next_route: page === "routes" ? result.next_route : displayed.next_route,
      } : result;
      currentDetail.current = next; setDetail(next);
    } catch (cause) {
      if (ownsRequest()) {
        currentDetail.current = null; setDetail(null);
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally { if (ownsRequest()) setBusy(false); }
  }

  async function command(choice?: "dismissed" | "keep_current") {
    const displayed = currentDetail.current;
    if (choice && (!displayed || !matchesSegmentationSnapshot(displayed, currentAdvice.current, repertoireId, displayed.recommendation.id))) {
      currentDetail.current = null; setDetail(null);
      setError("Load a fresh matching preview before saving this choice."); return;
    }
    const generation = ++commandGeneration.current;
    const listRequest = listGeneration.current;
    const ownsRequest = () => generation === commandGeneration.current && listRequest === listGeneration.current;
    const url = choice && displayed ? `${baseUrl}/${encodeURIComponent(displayed.recommendation.id)}/preference` : `${baseUrl}/refresh`;
    const body = choice && displayed ? { choice, content_version: displayed.content_version,
      graph_generation: displayed.graph_generation, source_fingerprint: displayed.source_fingerprint, snapshot_id: displayed.snapshot_id } : {};
    const payload = JSON.stringify([url, body]);
    if (pendingCommand.current?.payload !== payload) pendingCommand.current = { payload, key: crypto.randomUUID() };
    const pending = pendingCommand.current;
    setBusy(true); setError("");
    try {
      const response = await confirmOperationResponse(await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": pending.key },
        body: JSON.stringify(body), signal: AbortSignal.timeout(15_000),
      }));
      const result = await response.json() as { saved?: boolean; queued?: boolean; detail?: string };
      if (!ownsRequest()) return;
      if (response.status === 409) { currentDetail.current = null; setDetail(null); ++detailGeneration.current; }
      if (!response.ok || (choice ? result.saved !== true : typeof result.queued !== "boolean"))
        throw new Error(result.detail ?? "The service did not confirm this choice. Retry saving it.");
      if (pendingCommand.current === pending) pendingCommand.current = null;
      currentDetail.current = null; setDetail(null); setRevision(value => value + 1);
    } catch (cause) { if (ownsRequest()) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (ownsRequest()) setBusy(false); }
  }

  return <section className="opening-segmentation">
    <Button aria-expanded={opened} onClick={() => { ++listGeneration.current; ++detailGeneration.current; ++commandGeneration.current; currentDetail.current = null; currentAdvice.current = null; setOpened(value => !value); setDetail(null); }}>Recommended segmentation</Button>
    {opened && <div>
      <p><strong>Preview only</strong> · Your practice and review schedules stay the same.</p>
      {error && <Notice error>{error}</Notice>}
      {!advice && !error && <p>Loading recommendations…</p>}
      {advice?.state === "ready" && !advice.recommendations.length && <p>No new recommendations with sufficient structural savings.</p>}
      {Boolean(advice?.invalidated_pins) && <p>Content changed for a presentation you kept. Review its current boundaries before choosing again.</p>}
      {advice && advice.state !== "ready" && <p>{advice.state === "building" ? "Preparing recommendations in the background. Ordinary practice remains available." : "Recommendations need current repertoire preparation."}</p>}
      <Button disabled={busy} onClick={() => void command()}>{advice?.state === "building" ? "Check preparation" : "Refresh recommendations"}</Button>
      {advice?.state === "ready" && advice.recommendations.map(item => <div key={item.id}>
        <p>{item.kind === "shared_trunk" ? "Share the opening before branches" : "Share a transposed continuation"}: {item.decisions_before} → {item.decisions_after} tested decisions. {item.additional_starts} additional exercise starts.</p>
        <Button disabled={busy} onClick={() => void preview(item.id)}>Preview {item.kind === "shared_trunk" ? "shared opening" : "transposition"}</Button>
      </div>)}
      {detail && matchesSegmentationSnapshot(detail, advice, repertoireId, detail.recommendation.id) && <div>
        <p>{detail.rationale}</p><small>Structural move-count estimate. No measured time or learning improvement is implied.</small>
        <p>Affected routes: {detail.routes.map(route => route.name).join("; ")}</p>
        {detail.next_route && <Button disabled={busy} onClick={() => void preview(detail.recommendation.id, "routes")}>More affected routes</Button>}
        {detail.segments.map(segment => <div key={segment.id}>
          <h3>{roleLabels[segment.role]} · {segment.tested_decisions} tested {segment.tested_decisions === 1 ? "decision" : "decisions"}</h3>
          <p>Moves: {segment.moves.join(" ")}</p>
          <div className="segmentation-positions">{(["starting_fen", "ending_fen"] as const).map((field) => <div key={field}>
            <p>{field === "starting_fen" ? "Starts here" : "Ends here"}</p>
            <Chessboard owner={`segmentation:${segment.id}:${field}`} fen={segment[field]} locked showHint={false}
              theme={theme} pieceSet={pieceSet} orientation={segment.trained_color} onMove={() => {}} />
          </div>)}</div>
        </div>)}
        {detail.next_segment && <Button disabled={busy} onClick={() => void preview(detail.recommendation.id, "segments")}>More proposed segments</Button>}
        <Button disabled={busy} onClick={() => void command("dismissed")}>Dismiss recommendation</Button>
        <Button disabled={busy} onClick={() => void command("keep_current")}>Keep current presentation</Button>
      </div>}
    </div>}
  </section>;
}
