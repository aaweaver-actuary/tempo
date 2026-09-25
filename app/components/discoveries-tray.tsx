"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { Chess } from "chess.js";
import type { z } from "zod";
import { API_URL } from "../const";
import { Chessboard, type BoardTheme, type PieceSet } from "./chessboard";
import { MoveComparisonTable } from "./move-comparison-table";
import { discoveriesFeedSchema, discoveryRecommendationSchema } from "../domain/schemas";
import { adaptEngineMoves, adaptExplorerMoves } from "../domain/adapters/analysis-adapters";
import type { CandidateMove } from "../domain";
import { backgroundFetch } from "../lib/background-fetch";
import { requestInteractiveAnalysis } from "../lib/engine-broker";
import { loadExplorer, type ExplorerResult } from "../lib/lichess-explorer";
import { readLichessSessionToken } from "../lib/lichess-session";
import { readJsonResponse } from "../lib/validated-data";
import { usesLocalApi } from "../utils/local";

export type DiscoveryItem = z.infer<typeof discoveriesFeedSchema>["discoveries"][number];
type Recommendation = z.infer<typeof discoveryRecommendationSchema>;
type PreviewStatus = "waiting" | "unavailable" | "failed";

async function withConcurrency<T>(items: T[], limit: number, visit: (item: T) => Promise<void>) {
  let nextIndex = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (nextIndex < items.length) {
      const item = items[nextIndex++];
      if (item !== undefined) await visit(item);
    }
  }));
}

function decisionFen(discovery: DiscoveryItem): string {
  if (discovery.decision_fen) return discovery.decision_fen;
  if (discovery.kind !== "missing_response" || !discovery.opponent_move_uci) return discovery.fen;
  try {
    const board = new Chess(discovery.fen);
    board.move(discovery.opponent_move_uci);
    return board.fen();
  } catch { return discovery.fen; }
}

function moveSan(fen: string, uci: string): string {
  try {
    return new Chess(fen).move({ from: uci.slice(0, 2), to: uci.slice(2, 4),
      promotion: uci[4] })?.san ?? uci;
  } catch { return uci; }
}

function notation(fen: string, moves: string[]): string {
  try {
    const board = new Chess(fen);
    return moves.map((uci) => board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4),
      promotion: uci[4] })?.san ?? uci).join(" ");
  } catch { return moves.join(" "); }
}

function evidenceNumber(discovery: DiscoveryItem, key: string): string {
  return typeof discovery.evidence[key] === "number" ? String(discovery.evidence[key]) : "unavailable";
}

const recurringEvidenceKeys = [
  "window_days", "encounter_count", "analyzed_count", "miss_count",
  "strong_prefix_count", "sufficient_prefix_count", "immediate_average_loss_cp",
  "immediate_cp_sample_count", "later_average_change_cp", "later_sample_count",
] as const;

function hasCurrentRecurringEvidence(discovery: DiscoveryItem): boolean {
  return discovery.evidence.analysis_based !== true || (
    discovery.evidence.version === 2 && recurringEvidenceKeys.every((key) => key in discovery.evidence)
  );
}

function discoveryTitle(discovery: DiscoveryItem): string {
  if (discovery.kind === "missing_response") return "A response is missing from your repertoire";
  if (discovery.evidence.analysis_based)
    return discovery.evidence.strong_prefix_qualifies ? "Strong opening, weak next decision" : "Recurring weak decision";
  return "Opening decision to practice";
}

function recommendationMoves(fen: string, preview?: Recommendation): CandidateMove[] {
  const learnerSign = new Chess(fen).turn() === "w" ? 1 : -1;
  const lines = preview?.engine_lines ?? preview?.candidates.map((candidate) => ({
    move_uci: candidate.move_uci, score: candidate.score, depth: candidate.depth,
  })) ?? [];
  return lines.map((line) => ({
    uci: line.move_uci as CandidateMove["uci"],
    san: moveSan(fen, line.move_uci) as CandidateMove["san"],
    cp: line.score.cp === null ? undefined : line.score.cp * learnerSign,
    mate: line.score.mate === null ? undefined : line.score.mate * learnerSign,
    score: line.score.mate === null
      ? line.score.cp === null ? "—" : `${line.score.cp * learnerSign >= 0 ? "+" : ""}${((line.score.cp * learnerSign) / 100).toFixed(2)}`
      : `M${line.score.mate * learnerSign}`,
    depth: line.depth,
  }));
}

export function DiscoveriesTray({ safeToOpen, safeBreakCounter, interactionBlocked = false,
  onOpenRepertoire, onOpenBuilder, onQueueChanged, boardTheme = "brown", pieceSet = "cburnett",
  openRequest }: {
  safeToOpen: boolean; safeBreakCounter: number; interactionBlocked?: boolean;
  onOpenRepertoire?: () => void;
  onOpenBuilder?: (discovery: DiscoveryItem, selectedMove: string | null) => void;
  onQueueChanged: () => Promise<void>;
  boardTheme?: BoardTheme; pieceSet?: PieceSet;
  openRequest?: { id: string; token: number };
}) {
  const [open, setOpen] = useState(false);
  const [discoveries, setDiscoveries] = useState<DiscoveryItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectedMove, setSelectedMove] = useState<string | null>(null);
  const [hoveredMove, setHoveredMove] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, Recommendation>>({});
  const [previewFingerprints, setPreviewFingerprints] = useState<Record<string, string>>({});
  const [previewStatuses, setPreviewStatuses] = useState<Record<string, PreviewStatus>>({});
  const [feedLoaded, setFeedLoaded] = useState(() => !usesLocalApi());
  const [initialPreflightComplete, setInitialPreflightComplete] = useState(() => !usesLocalApi());
  const [explorer, setExplorer] = useState<ExplorerResult | null>(null);
  const [engine, setEngine] = useState<CandidateMove[]>([]);
  const [engineStatus, setEngineStatus] = useState("Waiting");
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [evidenceRefreshPendingId, setEvidenceRefreshPendingId] = useState<string | null>(null);
  const openedIds = useRef(new Set<string>());
  const suppressedIds = useRef(new Set<string>());
  const requestedEvidenceRefreshes = useRef(new Set<string>());
  const requestedPreflights = useRef(new Set<string>());
  const initialPreflightStarted = useRef(false);
  const pendingSafeBreak = useRef(false);
  const preflightState = useRef({ discoveries, previewFingerprints, previewStatuses });
  const lastSafeBreak = useRef(safeBreakCounter);
  const lastOpenRequestToken = useRef(openRequest?.token);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const readyDiscoveries = discoveries.filter((item) => {
    if (!initialPreflightComplete) return false;
    if (item.admission_state === "preparing") return false;
    if (item.card_id) return true;
    const recommendation = previews[item.id];
    return previewFingerprints[item.id] === item.evidence_fingerprint &&
      recommendation?.state === "ready" &&
      recommendation.evidence_fingerprint === item.evidence_fingerprint &&
      (recommendation.candidates?.length ?? 0) > 0;
  });
  const visibleDiscoveries = readyDiscoveries.filter((item) => !item.snoozed_until ||
    new Date(item.snoozed_until).getTime() <= currentTime);
  const activeIndex = activeId === null ? 0 : visibleDiscoveries.findIndex((item) => item.id === activeId);
  const active = activeIndex < 0 ? undefined : visibleDiscoveries[activeIndex];
  const fen = active ? decisionFen(active) : "";
  const preview = active ? previews[active.id] : undefined;

  useEffect(() => {
    preflightState.current = { discoveries, previewFingerprints, previewStatuses };
  }, [discoveries, previewFingerprints, previewStatuses]);

  const refresh = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const pages: Array<z.infer<typeof discoveriesFeedSchema>> = [];
      let offset: number | null = 0;
      while (offset !== null) {
        const response = await backgroundFetch(`${API_URL}/api/discoveries?offset=${offset}&limit=100`);
        const page = await readJsonResponse(response, discoveriesFeedSchema, "discoveries");
        pages.push(page);
        offset = page.next_offset;
      }
      const byId = new Map(pages.flatMap((page) => page.discoveries).map((item) => [item.id, item]));
      setDiscoveries([...byId.values()]);
      setEvidenceRefreshPendingId((pendingId) => {
        const refreshedItem = pendingId ? byId.get(pendingId) : undefined;
        return pendingId && (!refreshedItem || hasCurrentRecurringEvidence(refreshedItem)) ? null : pendingId;
      });
      setCurrentTime(Date.now());
      setFeedLoaded(true);
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load discoveries"); }
  }, []);

  useEffect(() => {
    if (!usesLocalApi()) return;
    const initialTimer = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), 5_000);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(interval); };
  }, [refresh]);

  const loadPreview = useCallback(async (item: DiscoveryItem): Promise<void> => {
    const key = `${item.id}:${item.evidence_fingerprint}`;
    if (requestedPreflights.current.has(key)) return;
    requestedPreflights.current.add(key);
    try {
      const response = await fetch(`${API_URL}/api/discoveries/${item.id}/recommendations`);
      const result = await readJsonResponse(response, discoveryRecommendationSchema, "continuation preview");
      setPreviews((current) => ({ ...current, [item.id]: result }));
      setPreviewFingerprints((current) => ({ ...current, [item.id]: item.evidence_fingerprint }));
      setPreviewStatuses((current) => {
        const remaining = { ...current };
        delete remaining[item.id];
        const unusableReadyResult = result.state === "ready" && (
          result.evidence_fingerprint !== item.evidence_fingerprint || !result.candidates?.length
        );
        const status = result.state === "waiting" || result.state === "unavailable"
          ? result.state : unusableReadyResult ? "failed" : null;
        return status ? { ...remaining, [item.id]: status } : remaining;
      });
    } catch {
      setPreviewFingerprints((current) => ({ ...current, [item.id]: item.evidence_fingerprint }));
      setPreviewStatuses((current) => ({ ...current, [item.id]: "failed" }));
    } finally {
      requestedPreflights.current.delete(key);
    }
  }, []);

  useEffect(() => {
    if (!feedLoaded) return;
    const preflightItems = discoveries.filter((item) => !item.card_id &&
      (previewFingerprints[item.id] !== item.evidence_fingerprint ||
        (!previews[item.id] && !previewStatuses[item.id])));
    if (!initialPreflightComplete) {
      if (initialPreflightStarted.current) return;
      initialPreflightStarted.current = true;
      void withConcurrency(preflightItems, 4, loadPreview).finally(() => setInitialPreflightComplete(true));
    } else if (preflightItems.length) {
      void withConcurrency(preflightItems, 4, loadPreview);
    }
  }, [feedLoaded, discoveries, previewFingerprints, previews, previewStatuses,
    initialPreflightComplete, loadPreview]);

  useEffect(() => {
    if (!initialPreflightComplete) return;
    const retryTimer = window.setInterval(() => {
      const { discoveries: currentDiscoveries, previewFingerprints: currentFingerprints,
        previewStatuses: currentStatuses } = preflightState.current;
      const waitingItems = currentDiscoveries.filter((item) => !item.card_id &&
        currentFingerprints[item.id] === item.evidence_fingerprint &&
        (currentStatuses[item.id] === "waiting" || currentStatuses[item.id] === "failed"));
      void withConcurrency(waitingItems, 4, loadPreview);
    }, 3_000);
    return () => window.clearInterval(retryTimer);
  }, [initialPreflightComplete, loadPreview]);

  useEffect(() => {
    if (!openRequest || openRequest.token === lastOpenRequestToken.current) return;
    lastOpenRequestToken.current = openRequest.token;
    setActiveId(openRequest.id);
    setOpen(true);
    void refresh();
  }, [openRequest, refresh]);

  useEffect(() => {
    const nextUnread = visibleDiscoveries.find((item) => item.unread &&
      !openedIds.current.has(item.id) && !suppressedIds.current.has(item.id));
    const reachedBreak = safeBreakCounter !== lastSafeBreak.current;
    lastSafeBreak.current = safeBreakCounter;
    if (interactionBlocked || open) {
      pendingSafeBreak.current = false;
      return;
    }
    if (safeToOpen || reachedBreak) pendingSafeBreak.current = true;
    if (!nextUnread || !pendingSafeBreak.current) return;
    pendingSafeBreak.current = false;
    setActiveId(nextUnread.id);
    setOpen(true);
  }, [visibleDiscoveries, safeToOpen, safeBreakCounter, interactionBlocked, open]);

  useEffect(() => {
    if (!open || !active?.unread || openedIds.current.has(active.id)) return;
    openedIds.current.add(active.id);
    void fetch(`${API_URL}/api/repertoires/${active.repertoire_id}/opportunities/${active.id}/acknowledge`,
      { method: "POST" })
      .then((response) => { if (!response.ok) throw new Error(`Could not acknowledge discovery (HTTP ${response.status})`); return refresh(); })
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not acknowledge discovery"));
  }, [open, active, refresh]);

  useEffect(() => {
    if (!open || !active || hasCurrentRecurringEvidence(active) ||
        requestedEvidenceRefreshes.current.has(active.id)) {
      return;
    }
    requestedEvidenceRefreshes.current.add(active.id);
    setEvidenceRefreshPendingId(active.id);
    void fetch(`${API_URL}/api/repertoires/${active.repertoire_id}/opportunities/refresh`, { method: "POST" })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({})) as { detail?: string };
          throw new Error(body.detail ?? `Could not refresh discovery evidence (HTTP ${response.status})`);
        }
      })
      .catch((cause) => {
        requestedEvidenceRefreshes.current.delete(active.id);
        setEvidenceRefreshPendingId(null);
        setError(cause instanceof Error ? cause.message : "Could not refresh discovery evidence");
      });
  }, [open, active, evidenceRefreshPendingId]);

  const closeViewer = useCallback(() => {
    pendingSafeBreak.current = false;
    for (const item of visibleDiscoveries) if (item.unread) suppressedIds.current.add(item.id);
    setOpen(false);
    triggerRef.current?.focus();
  }, [visibleDiscoveries]);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeViewer(); }
      if (event.key === "Tab") {
        const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ) ?? [])];
        if (!focusable.length) return;
        if (event.shiftKey && document.activeElement === focusable[0]) {
          event.preventDefault(); focusable.at(-1)?.focus();
        } else if (!event.shiftKey && document.activeElement === focusable.at(-1)) {
          event.preventDefault(); focusable[0].focus();
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, closeViewer]);

  const activeDiscoveryId = active?.id;
  const activeHasCard = Boolean(active?.card_id);
  useEffect(() => {
    if (!open || !activeDiscoveryId) return;
    let cancelled = false;
    const resetTimer = window.setTimeout(() => {
      if (cancelled) return;
      setSelectedMove(null);
      setHoveredMove(null);
      setExplorer(null);
      setEngine([]);
      setEngineStatus(activeHasCard ? "Analyzing" : "Preparing in Docker");
    }, 0);
    void loadExplorer(fen, "blitz,rapid,classical", "1600,1800,2000,2200,2500", readLichessSessionToken())
      .then((result) => { if (!cancelled) setExplorer(result); })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "Explorer unavailable"); });
    if (activeHasCard) {
      void requestInteractiveAnalysis(fen, 14)
        .then((moves) => { if (!cancelled) { setEngine(adaptEngineMoves(fen, moves.slice(0, 5))); setEngineStatus("Ready"); } })
        .catch((cause) => { if (!cancelled) setEngineStatus(cause instanceof Error ? cause.message : "Stockfish unavailable"); });
    }
    return () => { cancelled = true; window.clearTimeout(resetTimer); };
  }, [open, activeDiscoveryId, activeHasCard, fen]);

  const engineMoves = !active ? [] : active.card_id ? engine : recommendationMoves(fen, preview);
  const acceptedMoves = active?.accepted_moves_uci ?? preview?.accepted_moves_uci ?? [];
  const repertoireMoves = acceptedMoves.map((uci) => ({
    uci: uci as CandidateMove["uci"], san: moveSan(fen, uci) as CandidateMove["san"],
  }));
  const explorerMoves = active && explorer ? adaptExplorerMoves(fen, explorer.lichess.moves) : [];
  const mastersMoves = active && explorer ? adaptExplorerMoves(fen, explorer.masters.moves) : [];
  const engineLossCp: Record<string, number | null> = {};
  for (const line of preview?.engine_lines ?? []) engineLossCp[line.move_uci] = line.loss_cp;
  if (active?.card_id && engineMoves.length) {
    const best = engineMoves[0].cp;
    for (const move of engineMoves) engineLossCp[move.uci] = best === undefined || move.cp === undefined ? null : best - move.cp;
  }
  const soundSelection = preview?.candidates.find((candidate) => candidate.move_uci === selectedMove);
  const observedChange = active?.evidence.later_average_change_cp;
  const observedChangeText = typeof observedChange === "number"
    ? `${Math.abs(observedChange)} cp ${observedChange >= 0 ? "worse" : "better"}` : "unavailable";
  const mateOutcomes = Array.isArray(active?.evidence.mate_outcomes)
    ? active.evidence.mate_outcomes as Array<{ game_id?: string; preceding_move_mate?: number | null; third_later_turn_mate?: number | null }>
    : [];
  const displayedArrow = hoveredMove ?? selectedMove;
  const shapes = useMemo<DrawShape[]>(() => displayedArrow
    ? [{ orig: displayedArrow.slice(0, 2) as Key, dest: displayedArrow.slice(2, 4) as Key, brush: "green" }]
    : [], [displayedArrow]);

  const unreadCount = visibleDiscoveries.filter((item) => item.unread).length;
  const preflightPending = !initialPreflightComplete || discoveries.some((item) =>
    !item.card_id && previewFingerprints[item.id] === item.evidence_fingerprint &&
    (previewStatuses[item.id] === "waiting" || previewStatuses[item.id] === "failed"));
  const requestedDiscoveryNotReady = activeId !== null && activeIndex < 0;

  const act = async (item: DiscoveryItem, action: "train" | "snooze" | "dismiss") => {
    setBusyId(item.id);
    try {
      const response = await fetch(`${API_URL}/api/repertoires/${item.repertoire_id}/opportunities/${item.id}/${action}`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `${action} failed (HTTP ${response.status})`);
      }
      if (action === "train") await onQueueChanged();
      await refresh();
      if (action !== "train") setActiveId(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${action} discovery`); }
    finally { setBusyId(null); }
  };

  const accept = async (item: DiscoveryItem, moveUci: string) => {
    setBusyId(item.id);
    try {
      const response = await fetch(`${API_URL}/api/discoveries/${item.id}/accept`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_move_uci: moveUci, evidence_fingerprint: item.evidence_fingerprint }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `Could not add continuation (HTTP ${response.status})`);
      }
      await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not add continuation"); }
    finally { setBusyId(null); }
  };

  return <aside className="tempo-activity-tray tempo-discoveries-tray">
    <button ref={triggerRef} type="button" className="tempo-activity-trigger" aria-label="Discoveries"
      aria-expanded={open} onClick={() => {
        if (open) closeViewer();
        else { setActiveId(visibleDiscoveries[0]?.id ?? null); setOpen(true); }
      }}>
      Discoveries{unreadCount > 0 && <span className="tempo-discoveries-badge" aria-label={`${unreadCount} new discoveries`}> {unreadCount}</span>}
    </button>
    {open && <div className="tempo-discovery-backdrop">
      <section ref={dialogRef} className="tempo-discovery-viewer" role="dialog" aria-modal="true" aria-label="Discoveries">
        <header className="tempo-discovery-header">
          <div><span className="pill">Discoveries</span><h2>{active ? discoveryTitle(active) : requestedDiscoveryNotReady ? "This discovery is not ready for review" : preflightPending ? "Preparing review-ready discoveries" : "No discoveries ready for review"}</h2>
            <p>{active ? `${activeIndex + 1} of ${visibleDiscoveries.length} · ${active.trained_color} to move` : requestedDiscoveryNotReady ? "Choose Next to review a complete discovery." : preflightPending ? "Preparing review-ready discoveries." : "There are no complete discoveries to review right now."}</p></div>
          <div className="tempo-discovery-navigation">
            <button type="button" disabled={activeIndex <= 0} onClick={() => setActiveId(visibleDiscoveries[activeIndex - 1].id)}>Previous</button>
            <button type="button" disabled={activeIndex >= visibleDiscoveries.length - 1} onClick={() => setActiveId(visibleDiscoveries[activeIndex + 1].id)}>Next</button>
            <button ref={closeRef} type="button" onClick={closeViewer}>Back to work</button>
          </div>
        </header>
        {error && <p role="alert">{error} <button onClick={() => { setError(null); void refresh(); }}>Retry</button></p>}
        {active && <div className="tempo-discovery-main">
          <div className="tempo-discovery-board">
            <Chessboard owner="discoveries" fen={fen} orientation={active.trained_color} locked showHint={false}
              theme={boardTheme} pieceSet={pieceSet} shapes={shapes} onMove={() => undefined} />
            <p role="status">{displayedArrow ? `${moveSan(fen, displayedArrow)} selected. The green arrow shows its destination.` : "Select a move in the table to see it on the board."}</p>
            <div className="tempo-discovery-actions">
              {active.card_id && <button disabled={busyId === active.id || active.admission_state === "queued"}
                onClick={() => void act(active, "train")}>{active.admission_state === "queued" ? "In training queue" : "Train this decision"}</button>}
              {!active.card_id && <button disabled={!soundSelection || busyId === active.id || active.admission_state === "preparing"}
                onClick={() => { if (soundSelection) void accept(active, soundSelection.move_uci); }}>Add and train</button>}
              <button onClick={() => { closeViewer(); if (onOpenBuilder) onOpenBuilder(active, selectedMove); else onOpenRepertoire?.(); }}>Open in Builder</button>
            </div>
            {active.admission_state === "preparing" && <p role="status">Preparing training card. Tempo is publishing and checking the repertoire branch.</p>}
            {!active.card_id && selectedMove && !soundSelection && <p role="status">This move needs engine validation before Add and train is available. You can investigate it in Builder.</p>}
            {soundSelection && <p>Preview: {notation(fen, soundSelection.preview_moves_uci)} · {soundSelection.similarity}{soundSelection.example_line_name ? ` in ${soundSelection.example_line_name}` : ""}</p>}
          </div>
          <div className="tempo-discovery-detail">
            <p className="tempo-discovery-summary">{active.evidence.analysis_based
              ? `Past ${evidenceNumber(active, "window_days")} days: ${evidenceNumber(active, "encounter_count")} encounters, ${evidenceNumber(active, "miss_count")} confirmed mistakes in ${evidenceNumber(active, "analyzed_count")} analyzed decisions.`
              : `${evidenceNumber(active, "supporting_games")} supporting games. ${acceptedMoves.length ? "A continuation is saved." : "This response is missing from your repertoire."}`}</p>
            {active.kind === "missing_response" && active.opponent_move_uci && <p>After the opponent plays <strong>{moveSan(active.fen, active.opponent_move_uci)}</strong>, this is your decision.</p>}
            {active.routes[0] && <p>Example route: {active.routes[0]}{active.routes.length > 1 ? ` · ${active.routes.length} distinct routes reach this position` : ""}</p>}
            <p className="source-status" role="status">Stockfish: {active.card_id ? engineStatus : preview?.state === "ready" ? "Ready" : preview?.reason ?? "Preparing"} · Lichess: {explorer?.lichess.message ?? explorer?.lichess.state ?? "Loading"} · Masters: {explorer?.masters.message ?? explorer?.masters.state ?? "Loading"}</p>
            {evidenceRefreshPendingId === active.id && <p role="status">Refreshing older analysis evidence. This will update when the background refresh finishes.</p>}
            <p className="panel-message">Stockfish grades move quality. Explorer counts show what people played; popularity does not establish that a move is sound.</p>
            <MoveComparisonTable mode="discovery" repertoire={repertoireMoves} engine={engineMoves}
              engineLossCp={engineLossCp} lichess={explorerMoves} masters={mastersMoves} maia={[]}
              turn={active.trained_color} selectedMove={selectedMove} onPlay={setSelectedMove} onHover={setHoveredMove} />
            <details className="tempo-discovery-evidence"><summary>Why this position was flagged</summary>
              <p>Analysis coverage: {evidenceNumber(active, "analyzed_count")} of {evidenceNumber(active, "encounter_count")} encounters. Strong prefix: {evidenceNumber(active, "strong_prefix_count")} of {evidenceNumber(active, "sufficient_prefix_count")} sufficiently analyzed routes.</p>
              <p>Immediate loss: {active.evidence.immediate_cp_sample_count === 0 ? "no complete samples" : `${evidenceNumber(active, "immediate_average_loss_cp")} cp over ${evidenceNumber(active, "immediate_cp_sample_count")} complete samples`}. Observed change through your third later turn: {active.evidence.later_sample_count === 0 ? "no complete games" : `${observedChangeText} over ${evidenceNumber(active, "later_sample_count")} complete games`}. The later change is an observed outcome, not solely the result of one move.</p>
              {mateOutcomes.length > 0 && <ul>{mateOutcomes.map((outcome, index) => <li key={`${outcome.game_id ?? "game"}-${index}`}>
                {outcome.game_id ?? "Game"}: after the preceding move {outcome.preceding_move_mate === null || outcome.preceding_move_mate === undefined ? "no mate score" : `mate ${outcome.preceding_move_mate}`}; after your third later turn {outcome.third_later_turn_mate === null || outcome.third_later_turn_mate === undefined ? "no mate score" : `mate ${outcome.third_later_turn_mate}`}
              </li>)}</ul>}
              {active.source_games.length > 0 && <ul>{active.source_games.map((game) => <li key={game.id}>{game.url
                ? <a href={game.url} target="_blank" rel="noreferrer">{game.route || game.id}</a>
                : game.route || game.id}</li>)}</ul>}
            </details>
            <div className="tempo-discovery-secondary-actions">
              <button disabled={busyId === active.id} onClick={() => void act(active, "snooze")}>Snooze 7 days</button>
              <button disabled={busyId === active.id} onClick={() => void act(active, "dismiss")}>Dismiss</button>
            </div>
          </div>
        </div>}
      </section>
    </div>}
  </aside>;
}
