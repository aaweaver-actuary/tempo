import { Button } from "./buttons/BaseButton";
import { z } from "zod";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chess } from "chess.js";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { API_URL } from "../const";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import CloseButton from "./buttons/CloseButton";
import { useDialogFocus } from "../hooks/use-dialog-focus";
import { loadExplorer } from "../lib/lichess-explorer";
import { readLichessSessionToken } from "../lib/lichess-session";
import { adaptExplorerMoves } from "../domain/adapters/analysis-adapters";
import type { CandidateMove } from "../domain";
import { integrityRecommendationSchema, repertoireIntegritySchema } from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { enqueueIntegrityRepair, pendingIntegrityRepairs } from "../lib/integrity-repair-outbox";
import { backgroundFetch } from "../lib/background-fetch";
import { asFenString, asUciMove, asSanMove } from "../types";
import { MoveComparisonTable } from "./move-comparison-table";

function sanAt(fen: string, moveUci: string) {
  try { return new Chess(fen).move(moveUci).san; } catch { return moveUci; }
}

export function RepertoireIntegrityDialog({ repertoireId, theme, pieceSet, onClose }: {
  repertoireId: string; theme: BoardTheme; pieceSet: PieceSet; onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, onClose);
  const [payload, setPayload] = useState<ReturnType<typeof repertoireIntegritySchema.parse>>();
  const [selected, setSelected] = useState<string>();
  const [hovered, setHovered] = useState<string | null>(null);
  const [preview, setPreview] = useState<ReturnType<typeof integrityRecommendationSchema.parse>>();
  const [lichess, setLichess] = useState<CandidateMove[]>([]);
  const [masters, setMasters] = useState<CandidateMove[]>([]);
  const [sourceStatus, setSourceStatus] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [recommendationError, setRecommendationError] = useState("");
  const [recommendationRetry, setRecommendationRetry] = useState(0);
  const [continuationStep, setContinuationStep] = useState(0);
  const [historyStep, setHistoryStep] = useState<number | null>(null);
  const issue = payload?.issues[0];
  const issueIdentity = issue ? JSON.stringify([repertoireId, issue.id, issue.signature, issue.fen]) : "";
  const board = useMemo(() => {
    try { return issue?.fen ? new Chess(issue.fen) : undefined; } catch { return undefined; }
  }, [issue]);
  const choose = useCallback((moveUci: string) => {
    if (!board) return;
    try { new Chess(board.fen()).move(moveUci); } catch { return; }
    setSelected(moveUci); setHovered(null); setContinuationStep(0); setHistoryStep(null);
  }, [board]);

  useEffect(() => {
    const controller = new AbortController(); let timer: number | undefined;
    const load = async () => {
      try {
        const response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/integrity`, { signal: controller.signal });
        const next = await readJsonResponse(response, repertoireIntegritySchema, "repertoire integrity");
        if (controller.signal.aborted) return;
        setPayload(next);
        if (["queued", "running", "retrying"].includes(next.scan_status)) timer = window.setTimeout(() => void load(), 3_000);
      } catch (failure) {
        if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Integrity data unavailable.");
      }
    };
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [repertoireId]);

  useEffect(() => {
    let current = true;
    queueMicrotask(() => { if (current) { setSelected(undefined); setHovered(null); setContinuationStep(0); setHistoryStep(null); } });
    return () => { current = false; };
  }, [issueIdentity]);

  useEffect(() => {
    const controller = new AbortController(); let timer: number | undefined;
    let preparationOperation: string | undefined;
    queueMicrotask(() => { if (!controller.signal.aborted) {
      setPreview(undefined); setLichess([]); setMasters([]); setSourceStatus([]); setRecommendationError("");
    } });
    if (!issue?.fen || !board) return () => controller.abort();
    const endpoint = `${API_URL}/api/repertoires/${encodeURIComponent(repertoireId)}/integrity/issues/${encodeURIComponent(issue.id)}/recommendations`;
    const loadRecommendation = async (prepare = false) => {
      try {
        if (prepare) {
          const admission = await fetch(endpoint, { method: "POST", signal: controller.signal,
            headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
            body: JSON.stringify({ signature: issue.signature }) });
          if (!admission.ok) throw new Error(`Recommendation preparation failed (HTTP ${admission.status}). Refresh repair evidence.`);
          const accepted = z.object({ operation_id: z.string().optional() }).parse(await admission.json());
          preparationOperation = accepted.operation_id;
        }
        if (preparationOperation) {
          const receipt = await backgroundFetch(`${API_URL}/api/operations/${encodeURIComponent(preparationOperation)}`, { signal: controller.signal });
          if (!receipt.ok) throw new Error(`Recommendation delivery could not be checked (HTTP ${receipt.status}). Retry suggestion.`);
          const operation = z.object({ state: z.string(), error: z.object({ message: z.string().optional() }).optional(),
            last_error: z.object({ message: z.string().optional() }).optional() }).parse(await receipt.json());
          if (["failed", "blocked"].includes(operation.state)) throw new Error(operation.error?.message ?? operation.last_error?.message ?? "Recommendation preparation failed. Check Analysis activity.");
          if (operation.state !== "complete") {
            timer = window.setTimeout(() => void loadRecommendation(), 3_000);
            return;
          }
          preparationOperation = undefined;
        }
        const response = await backgroundFetch(`${endpoint}?signature=${encodeURIComponent(issue.signature)}`, { signal: controller.signal });
        const result = await readJsonResponse(response, integrityRecommendationSchema, "repair recommendation");
        if (controller.signal.aborted) return;
        if (result.repertoire_id !== repertoireId || result.issue_id !== issue.id || result.signature !== issue.signature ||
            result.state === "ready" && (!result.starting_fen || new Chess(result.starting_fen).fen().split(" ").slice(0, 4).join(" ") !== board.fen().split(" ").slice(0, 4).join(" ")))
          throw new Error("Recommendation evidence changed. Refresh repertoire repair.");
        setPreview(result);
        if (result.state === "waiting") timer = window.setTimeout(() => void loadRecommendation(), 3_000);
      } catch (failure) {
        if (!controller.signal.aborted) setRecommendationError(failure instanceof Error ? failure.message : "Recommendation unavailable.");
      }
    };
    void loadRecommendation(true);
    void fetch(`${API_URL}/api/games/position-summary?fen=${encodeURIComponent(issue.fen)}`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error(`Personal games unavailable (HTTP ${response.status})`);
        const games = await response.json() as { moves?: { move_uci: string; games: number; score_percentage: number }[] };
        if (!controller.signal.aborted) setSourceStatus(status => [...status, games.moves?.length ?
          `Personal games: ${games.moves.map(move => `${sanAt(issue.fen!, move.move_uci)} · ${move.games} games · ${move.score_percentage}%`).join("; ")}` : "Personal games: no games here"]);
      }).catch(failure => { if (!controller.signal.aborted) setSourceStatus(status => [...status, String(failure.message ?? failure)]); });
    void loadExplorer(issue.fen, "blitz,rapid,classical", "1600,1800,2000,2200,2500", readLichessSessionToken())
      .then(result => {
        if (controller.signal.aborted) return;
        if (result) {
          setLichess(adaptExplorerMoves(issue.fen!, result.lichess.moves));
          setMasters(adaptExplorerMoves(issue.fen!, result.masters.moves));
          setSourceStatus(status => [...status, `Lichess: ${result.lichess.message ?? result.lichess.state}`, `Masters: ${result.masters.message ?? result.masters.state}`]);
        } else setSourceStatus(status => [...status, "Explorer unavailable; reconnect Lichess in Builder."]);
      }).catch(failure => { if (!controller.signal.aborted) setSourceStatus(status => [...status, String(failure.message ?? failure)]); });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [issue, board, repertoireId, recommendationRetry]);

  const continuation = useMemo(() => preview?.candidates.find(candidate => candidate.move_uci === selected)?.preview_moves_uci ??
    (selected ? [selected] : []), [preview, selected]);
  const route = useMemo(() => preview?.route_uci ?? [], [preview]);
  const rendered = useMemo(() => {
    if (!board) return undefined;
    const position = historyStep !== null && preview?.route_start_fen ? new Chess(preview.route_start_fen) : new Chess(board.fen());
    const moves = historyStep !== null ? route.slice(0, historyStep) : continuation.slice(0, continuationStep);
    try { for (const moveUci of moves) position.move(moveUci); } catch { return { position: new Chess(board.fen()), lastMove: undefined }; }
    const last = moves.at(-1);
    return { position, lastMove: last ? [last.slice(0, 2), last.slice(2, 4)] as const : undefined };
  }, [board, preview, route, historyStep, continuation, continuationStep]);
  const shapes = useMemo<DrawShape[]>(() => {
    if (!rendered || historyStep !== null) return [];
    const nextMove = continuationStep === 0 ? hovered ?? selected : continuation[continuationStep];
    if (!nextMove) return [];
    try { new Chess(rendered.position.fen()).move(nextMove); } catch { return []; }
    return [{ orig: nextMove.slice(0, 2) as Key, dest: nextMove.slice(2, 4) as Key, brush: "green" }];
  }, [rendered, historyStep, continuationStep, hovered, selected, continuation]);
  const next = () => historyStep !== null ? setHistoryStep(historyStep + 1 < route.length ? historyStep + 1 : null)
    : setContinuationStep(step => Math.min(continuation.length, step + 1));
  const previous = () => historyStep !== null ? setHistoryStep(Math.max(0, historyStep - 1)) : continuationStep > 0
    ? setContinuationStep(step => step - 1) : route.length > 0 && setHistoryStep(route.length - 1);
  let pending = false;
  try { pending = pendingIntegrityRepairs().some(repair => repair.repertoireId === repertoireId && repair.issueId === issue?.id); }
  catch { /* Saving reports the preserved storage error. */ }
  const repertoireMoves = useMemo<CandidateMove[]>(() => (issue?.moves ?? []).map(move => ({
    uci: asUciMove(move.uci), san: asSanMove(sanAt(issue!.fen!, move.uci)) })), [issue]);
  const engineMoves = useMemo<CandidateMove[]>(() => (preview?.engine_lines ?? []).map(line => ({
    uci: asUciMove(line.move_uci), san: asSanMove(sanAt(issue!.fen!, line.move_uci)),
    ...(line.score.cp !== null ? { cp: line.score.cp } : {}), ...(line.score.mate !== null ? { mate: line.score.mate } : {}),
  })), [preview, issue]);
  function save() {
    if (!issue || !selected) return;
    try {
      enqueueIntegrityRepair({ repertoireId, issueId: issue.id, signature: issue.signature, selectedMoveUci: selected });
      onClose();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Could not store the repair choice. Try again."); }
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="ui-dialog integrity-dialog" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true"
      aria-labelledby="integrity-title" onMouseDown={event => event.stopPropagation()}>
      <CloseButton onClose={onClose} ariaLabel="Defer repertoire repair" />
      <p className="eyebrow">Repertoire repair</p><h2 id="integrity-title">Choose one response per position</h2>
      {!payload && !error && <p>Checking saved lines…</p>}
      {error && <p className="editor-error" role="alert">{error}</p>}
      {payload && !issue && <p role="status">{payload.scan_status === "idle" && payload.status === "clean" ? "This repertoire is clean." :
        payload.scan_status === "failed" ? `Validation failed: ${payload.last_scan_error ?? "Check Analysis activity and retry validation."}` :
          "Repertoire validation is still running. You can keep studying and resume repair when it finishes."}</p>}
      {issue && <><p className="dialog-copy">{payload.issue_count} issues remaining · {issue.kind === "missing_response" ? "a response is missing" :
        issue.kind === "multiple_responses" ? "multiple responses are saved" : "a source is invalid"}</p>
        {board && rendered ? <div className="integrity-layout"><div className="integrity-board">
          <Chessboard fen={asFenString(rendered.position.fen())} locked={historyStep !== null || continuationStep > 0 || pending}
            showHint={false} theme={theme} pieceSet={pieceSet} orientation={issue.trained_color ?? "white"}
            shapes={shapes} lastMove={rendered.lastMove} showShortcutButton={false}
            keyboard={{ previous, next, start: () => { setHistoryStep(null); setContinuationStep(0); },
              end: () => { setHistoryStep(null); setContinuationStep(continuation.length); } }}
            onMove={(from, to) => choose(`${from}${to}` + (board.get(from)?.type === "p" && (to[1] === "1" || to[1] === "8") ? "q" : ""))} />
          <div className="repair-preview-controls" role="group" aria-label="Repair preview navigation">
            <Button onClick={previous} disabled={continuationStep === 0 && (historyStep === 0 || route.length === 0)}>Previous move</Button>
            <Button onClick={() => { setHistoryStep(null); setContinuationStep(0); }}>Decision position</Button>
            <Button onClick={next} disabled={historyStep === null && continuationStep >= continuation.length}>Next move</Button>
          </div>
        </div><div className="integrity-evidence">
          <p><strong>Saved responses</strong>: {issue.moves.length ? issue.moves.map(move =>
            `${sanAt(issue.fen!, move.uci)} (${move.line_count} lines, ${move.card_count} cards, ${move.review_count} reviews)`).join(" · ") : "none"}</p>
          <p className="source-status">Affected sources: {issue.sources.map(source => `${source.type} ${source.id}`).join(" · ") || "none"}</p>
          {preview?.suggested_move_uci && <div className="repair-suggestion" role="status"><strong>Suggested response: {sanAt(issue.fen!, preview.suggested_move_uci)}</strong>
            <p>{preview.suggestion_reason}</p><Button onClick={() => choose(preview.suggested_move_uci!)}>Use suggested response</Button></div>}
          {(recommendationError || preview?.reason || !preview) && <p className="source-status" role="status">
            {recommendationError || preview?.reason || "Preparing a suggestion…"}</p>}
          {(recommendationError || preview?.state === "failed") && <Button onClick={() => setRecommendationRetry(count => count + 1)}>Retry suggestion</Button>}
          <MoveComparisonTable repertoire={repertoireMoves} engine={engineMoves} maia={[]} lichess={lichess} masters={masters}
            turn={board.turn() === "w" ? "white" : "black"} onPlay={choose} onHover={setHovered} selectedMove={selected}
            mode="discovery" engineLossCp={Object.fromEntries((preview?.engine_lines ?? []).map(line => [line.move_uci, line.loss_cp]))} />
          <p className="source-status">{sourceStatus.join(" · ")}</p>
          <p>Selected response: <strong>{selected ? sanAt(issue.fen!, selected) : "Choose a legal move"}</strong></p>
          <p className="source-status">Keep this response replaces competing responses and truncates incompatible continuations. Unchanged review history is preserved.</p>
          {pending && <p role="status">This repair is saving in the background. You can keep studying.</p>}
          <Button variant="primary" className="primary-button" disabled={!selected || pending} onClick={save}>Keep this response</Button>
        </div></div> : <p role="alert">This source cannot be read. Edit or remove the invalid line/card, then resume repair.</p>}
      </>}
    </section>
  </div>;
}
