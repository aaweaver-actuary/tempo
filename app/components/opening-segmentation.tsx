import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Button } from "./buttons/BaseButton";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import { Notice } from "./task-tabs";
import { API_URL } from "../const";
import { confirmOperationResponse } from "../lib/operation-status";
import { segmentationDetailSchema, segmentationListSchema, type SegmentationDetail } from "../domain/opening-segmentation";

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
  const requestGeneration = useRef(0);
  const pendingCommand = useRef<{ payload: string; key: string } | null>(null);
  const baseUrl = `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/segmentation`;
  useEffect(() => {
    if (!opened) return;
    const controller = new AbortController();
    const generation = ++requestGeneration.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const started = Date.now();
    async function load() {
      try {
        const result = await readAdvice(baseUrl, segmentationListSchema, AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]));
        if (controller.signal.aborted || generation !== requestGeneration.current) return;
        setAdvice(result);
        setError(result.error ?? "");
        if (result.state === "building" && Date.now() - started < 60_000) timer = setTimeout(() => void load(), 2_000);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
      }
    }
    void load();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [opened, revision, baseUrl]);

  async function preview(id: string, page?: "segments" | "routes") {
    const generation = requestGeneration.current;
    setBusy(true); setError("");
    try {
      const cursor = page === "segments" ? `?after_segment=${encodeURIComponent(detail?.next_segment ?? "")}`
        : page === "routes" ? `?after_route=${encodeURIComponent(detail?.next_route ?? "")}` : "";
      const result = await readAdvice(`${baseUrl}/${encodeURIComponent(id)}${cursor}`, segmentationDetailSchema);
      if (generation !== requestGeneration.current) return;
      setDetail(page && detail ? { ...result,
        segments: page === "segments" ? [...detail.segments, ...result.segments] : detail.segments,
        next_segment: page === "segments" ? result.next_segment : detail.next_segment,
        routes: page === "routes" ? [...detail.routes, ...result.routes] : detail.routes,
        next_route: page === "routes" ? result.next_route : detail.next_route,
      } : result);
    } catch (cause) { if (generation === requestGeneration.current) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  async function command(choice?: "dismissed" | "keep_current") {
    const url = choice && detail ? `${baseUrl}/${detail.recommendation.id}/preference` : `${baseUrl}/refresh`;
    const body = choice && detail ? { choice, content_version: detail.content_version,
      graph_generation: detail.graph_generation, source_fingerprint: detail.source_fingerprint } : {};
    const payload = JSON.stringify([url, body]);
    if (pendingCommand.current?.payload !== payload) pendingCommand.current = { payload, key: crypto.randomUUID() };
    setBusy(true); setError("");
    try {
      const response = await confirmOperationResponse(await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": pendingCommand.current.key },
        body: JSON.stringify(body), signal: AbortSignal.timeout(15_000),
      }));
      const result = await response.json() as { saved?: boolean; queued?: boolean; detail?: string };
      if (!response.ok || (choice ? result.saved !== true : typeof result.queued !== "boolean"))
        throw new Error(result.detail ?? "The service did not confirm this choice. Retry saving it.");
      pendingCommand.current = null;
      setDetail(null); setRevision(value => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  }

  return <section className="opening-segmentation">
    <Button aria-expanded={opened} onClick={() => { setOpened(value => !value); setDetail(null); }}>Recommended segmentation</Button>
    {opened && <div>
      <p><strong>Preview only</strong> · Your practice and review schedules stay the same.</p>
      {error && <Notice error>{error}</Notice>}
      {!advice && !error && <p>Loading recommendations…</p>}
      {advice?.state === "ready" && !advice.recommendations.length && <p>No new recommendations with sufficient structural savings.</p>}
      {Boolean(advice?.invalidated_pins) && <p>Content changed for a presentation you kept. Review its current boundaries before choosing again.</p>}
      {advice && advice.state !== "ready" && <p>{advice.state === "building" ? "Preparing recommendations in the background. Ordinary practice remains available." : "Recommendations need current repertoire preparation."}</p>}
      <Button disabled={busy} onClick={() => void command()}>{advice?.state === "building" ? "Check preparation" : "Refresh recommendations"}</Button>
      {advice?.recommendations.map(item => <div key={item.id}>
        <p>{item.kind === "shared_trunk" ? "Share the opening before branches" : "Share a transposed continuation"}: {item.decisions_before} → {item.decisions_after} tested decisions. {item.additional_starts} additional exercise starts.</p>
        <Button disabled={busy} onClick={() => void preview(item.id)}>Preview {item.kind === "shared_trunk" ? "shared opening" : "transposition"}</Button>
      </div>)}
      {detail && <div>
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
