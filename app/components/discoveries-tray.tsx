"use client";
import { Button } from "./buttons/BaseButton";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DrawShape } from "@lichess-org/chessground/draw";
import type { Key } from "@lichess-org/chessground/types";
import { Chess } from "chess.js";
import type { z } from "zod";
import { API_URL } from "../const";
import { Chessboard, type BoardTheme, type PieceSet } from "./board/chessboard";
import { MoveComparisonTable } from "./move-comparison-table";
import { discoveriesFeedSchema, discoveryRecommendationSchema,
  discoveryTrainingEligibilitySchema } from "../domain/schemas";
import { adaptEngineMoves, adaptExplorerMoves } from "../domain/adapters/analysis-adapters";
import type { CandidateMove } from "../domain";
import { backgroundFetch } from "../lib/background-fetch";
import {
  DISCOVERY_ADMISSIONS_CHANGED,
  DISCOVERY_ADMISSION_QUEUED,
  enqueuePendingDiscoveryAdmission,
  flushPendingDiscoveryAdmissions,
  pendingDiscoveryAdmissions,
  recoverUnacknowledgedDiscoveryAdmissions,
  retryPendingDiscoveryAdmission,
  type PendingDiscoveryAdmission,
} from "../lib/discovery-admission-outbox";
import { requestInteractiveAnalysis } from "../lib/engine-broker";
import { loadExplorer, type ExplorerResult } from "../lib/lichess-explorer";
import { readLichessSessionToken } from "../lib/lichess-session";
import { readJsonResponse } from "../lib/validated-data";
import { reportDebugError } from "../lib/debug-reporting";
import { applyOpportunityCommand } from "../lib/opportunity-command";
import { requestOpportunityRefresh } from "../lib/opportunity-refresh-command";
import { usesLocalApi } from "../utils/local";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";

export type DiscoveryItem = z.infer<typeof discoveriesFeedSchema>["discoveries"][number];
type Recommendation = z.infer<typeof discoveryRecommendationSchema>;
type TrainingEligibility = z.infer<typeof discoveryTrainingEligibilitySchema>;
type PreviewStatus = "waiting" | "unavailable" | "failed";
const previewRetryDelayMs = 30_000;
const previewConcurrency = 2;

async function withConcurrency<T>(items: T[], limit: number, visit: (item: T) => Promise<void>) {
  let nextIndex = 0;
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (nextIndex < items.length) {
      const item = items[nextIndex++];
      if (item !== undefined) await visit(item);
    }
  }));
}

async function inactiveDiscoveryPreview(response: Response): Promise<boolean> {
  if (response.status !== 404) return false;
  try {
    const body: unknown = await response.clone().json();
    if (typeof body !== "object" || body === null || !("detail" in body)) return false;
    return body.detail === "Active discovery not found" || body.detail === "'Active discovery not found'";
  } catch { return false; }
}

function eligibilityKey(item: DiscoveryItem): string {
  return `${item.id}:${item.evidence_fingerprint}:${item.card_id ?? ""}`;
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

function decisionMoves(fen: string): Set<string> {
  return new Set(new Chess(fen).moves({ verbose: true }).map((move) =>
    `${move.from}${move.to}${move.promotion ?? ""}`));
}

function previewMatchesDecision(discovery: DiscoveryItem, preview: Recommendation): boolean {
  if (preview.state !== "ready" || !preview.starting_fen) return false;
  try {
    const decisionPosition = decisionFen(discovery);
    if (preview.starting_fen.split(" ").slice(0, 4).join(" ") !==
        decisionPosition.split(" ").slice(0, 4).join(" ")) return false;
    const decisionBoard = new Chess(decisionPosition);
    if ((decisionBoard.turn() === "w" ? "white" : "black") !== discovery.trained_color) return false;
    const legalMoves = decisionMoves(decisionPosition);
    return Boolean(preview.candidates.length) &&
      preview.candidates.every((candidate) => legalMoves.has(candidate.move_uci)) &&
      (!preview.suggested_move_uci || legalMoves.has(preview.suggested_move_uci));
  } catch { return false; }
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

type BoardStep = {
  fen: string;
  moveUci: string | null;
  moveSan: string | null;
};

function discoveryBoardHistory(
  discovery: DiscoveryItem,
  decisionPosition: string,
  previewMoves: string[],
): { steps: BoardStep[]; decisionIndex: number } {
  const startFen = discovery.decision_start_fen ?? decisionPosition;
  const route = discovery.decision_route_uci ?? [];
  let steps: BoardStep[] = [{ fen: decisionPosition, moveUci: null, moveSan: null }];
  try {
    const board = new Chess(startFen);
    steps = [{ fen: board.fen(), moveUci: null, moveSan: null }];
    for (const moveUci of route) {
      const move = board.move({
        from: moveUci.slice(0, 2),
        to: moveUci.slice(2, 4),
        promotion: moveUci[4],
      });
      steps.push({ fen: board.fen(), moveUci, moveSan: move.san });
    }
    if (
      board.fen().split(" ").slice(0, 4).join(" ") !==
      new Chess(decisionPosition).fen().split(" ").slice(0, 4).join(" ")
    ) {
      throw new Error("Discovery route does not reach the decision");
    }
  } catch {
    steps = [{ fen: decisionPosition, moveUci: null, moveSan: null }];
  }
  const decisionIndex = steps.length - 1;
  steps[decisionIndex] = { ...steps[decisionIndex], fen: decisionPosition };
  const previewBoard = new Chess(decisionPosition);
  for (const moveUci of previewMoves) {
    try {
      const move = previewBoard.move({
        from: moveUci.slice(0, 2),
        to: moveUci.slice(2, 4),
        promotion: moveUci[4],
      });
      steps.push({ fen: previewBoard.fen(), moveUci, moveSan: move.san });
    } catch {
      break;
    }
  }
  return { steps, decisionIndex };
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
  const [sessionItems, setSessionItems] = useState<DiscoveryItem[]>([]);
  const [manualSelections, setManualSelections] = useState<
    Record<string, string>
  >({});
  const [boardNavigation, setBoardNavigation] = useState<{
    discoveryId: string;
    cursor: number;
  } | null>(null);
  const [pendingAdmissions, setPendingAdmissions] = useState<
    PendingDiscoveryAdmission[]
  >([]);
  const [hoveredMove, setHoveredMove] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, Recommendation>>({});
  const [previewFingerprints, setPreviewFingerprints] = useState<Record<string, string>>({});
  const [previewStatuses, setPreviewStatuses] = useState<Record<string, PreviewStatus>>({});
  const [trainingEligibility, setTrainingEligibility] = useState<Record<string, TrainingEligibility>>({});
  const [eligibilityErrors, setEligibilityErrors] = useState<Record<string, string>>({});
  const [feedLoaded, setFeedLoaded] = useState(() => !usesLocalApi());
  const [initialPreflightComplete, setInitialPreflightComplete] = useState(() => !usesLocalApi());
  const [initialAdmissionFlushFinished, setInitialAdmissionFlushFinished] = useState(() => !usesLocalApi());
  const [explorer, setExplorer] = useState<ExplorerResult | null>(null);
  const [engine, setEngine] = useState<CandidateMove[]>([]);
  const [engineStatus, setEngineStatus] = useState("Waiting");
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const [evidenceRefreshPendingId, setEvidenceRefreshPendingId] = useState<string | null>(null);
  const openedIds = useRef(new Set<string>());
  const suppressedIds = useRef(new Set<string>());
  const requestedEvidenceRefreshes = useRef(new Set<string>());
  const requestedPreflights = useRef(new Set<string>());
  const nextPreflightRetryAt = useRef(new Map<string, number>());
  const stalePreviewKeys = useRef(new Set<string>());
  const currentFeedItems = useRef(new Map<string, DiscoveryItem>());
  const previewGenerations = useRef(new Map<string, number>());
  const eligibilityRequests = useRef(new Set<string>());
  const initialPreflightStarted = useRef(false);
  const pendingSafeBreak = useRef(false);
  const preflightState = useRef({ discoveries, previewFingerprints, previewStatuses });
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const staleRefreshInFlight = useRef<Promise<void> | null>(null);
  const lastSafeBreak = useRef(safeBreakCounter);
  const lastOpenRequestToken = useRef(openRequest?.token);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const [completedAdmissions, setCompletedAdmissions] = useState<string[]>([]);
  const outboxRecoveryStarted = useRef(false);
  const readyDiscoveries = useMemo(() => discoveries.filter((item) => {
    if (!initialPreflightComplete) return false;
    if (item.admission_state === "preparing") return false;
    if (item.card_id) return true;
    const recommendation = previews[item.id];
    return previewFingerprints[item.id] === item.evidence_fingerprint &&
      recommendation?.state === "ready" &&
      recommendation.evidence_fingerprint === item.evidence_fingerprint &&
      previewMatchesDecision(item, recommendation);
  }), [discoveries, initialPreflightComplete, previews, previewFingerprints]);
  const visibleDiscoveries = readyDiscoveries.filter((item) => !item.snoozed_until ||
    new Date(item.snoozed_until).getTime() <= currentTime);
  const reviewItems = sessionItems.length ? sessionItems : visibleDiscoveries;
  const activeIndex =
    activeId === null
      ? -1
      : reviewItems.findIndex((item) => item.id === activeId);
  const activeSnapshot =
    activeIndex < 0 ? undefined : reviewItems[activeIndex];
  const active = activeSnapshot
    ? (discoveries.find((item) => item.id === activeSnapshot.id) ??
      activeSnapshot)
    : undefined;
  useEffect(() => {
    if (open && activeId && !currentFeedItems.current.has(activeId) && reviewItems.length)
      setActiveId(reviewItems[0].id);
  }, [open, activeId, reviewItems]);
  const fen = active ? decisionFen(active) : "";
  const legalDecisionMoves = useMemo(() => fen ? decisionMoves(fen) : new Set<string>(), [fen]);
  const savedPreview = active ? previews[active.id] : undefined;
  const preview = active && savedPreview?.state === "ready" && !previewMatchesDecision(active, savedPreview)
    ? undefined : savedPreview;
  const selectedMove = active
    ? (manualSelections[active.id] ??
      preview?.suggested_move_uci ??
      preview?.candidates[0]?.move_uci ??
      null)
    : null;

  const openViewer = useCallback(
    (requestedId: string | null) => {
      setSessionItems(visibleDiscoveries);
      setActiveId(requestedId ?? visibleDiscoveries[0]?.id ?? null);
      setOpen(true);
    },
    [visibleDiscoveries],
  );

  useEffect(() => {
    if (!open || sessionItems.length || !visibleDiscoveries.length) return;
    const timer = window.setTimeout(() => {
      setSessionItems(visibleDiscoveries);
      if (activeId === null) setActiveId(visibleDiscoveries[0].id);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, sessionItems.length, visibleDiscoveries, activeId]);


  useEffect(() => {
    preflightState.current = { discoveries, previewFingerprints, previewStatuses };
  }, [discoveries, previewFingerprints, previewStatuses]);

  const loadFeed = useCallback(async (): Promise<void> => {
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
      const currentPreviewKeys = new Set([...byId.values()].map((item) =>
        `${item.id}:${item.evidence_fingerprint}`));
      for (const id of new Set([...currentFeedItems.current.keys(), ...byId.keys()])) {
        const previousFingerprint = currentFeedItems.current.get(id)?.evidence_fingerprint;
        const nextFingerprint = byId.get(id)?.evidence_fingerprint;
        if (previousFingerprint !== nextFingerprint)
          previewGenerations.current.set(id, (previewGenerations.current.get(id) ?? 0) + 1);
      }
      currentFeedItems.current = byId;
      for (const key of nextPreflightRetryAt.current.keys())
        if (!currentPreviewKeys.has(key)) nextPreflightRetryAt.current.delete(key);
      for (const key of stalePreviewKeys.current)
        if (!currentPreviewKeys.has(key)) stalePreviewKeys.current.delete(key);
      const currentPreviewIds = new Set(Object.entries(preflightState.current.previewFingerprints)
        .filter(([id, fingerprint]) => byId.get(id)?.evidence_fingerprint === fingerprint)
        .map(([id]) => id));
      setPreviews((current) => Object.fromEntries(Object.entries(current).filter(
        ([id]) => currentPreviewIds.has(id))));
      setPreviewFingerprints((current) => Object.fromEntries(Object.entries(current).filter(
        ([id, fingerprint]) => byId.get(id)?.evidence_fingerprint === fingerprint)));
      setPreviewStatuses((current) => Object.fromEntries(Object.entries(current).filter(
        ([id]) => currentPreviewIds.has(id))));
      const currentEligibilityKeys = new Set([...byId.values()].map(eligibilityKey));
      setTrainingEligibility((current) => Object.fromEntries(Object.entries(current).filter(
        ([key]) => currentEligibilityKeys.has(key))));
      setEligibilityErrors((current) => Object.fromEntries(Object.entries(current).filter(
        ([key]) => currentEligibilityKeys.has(key))));
      setDiscoveries([...byId.values()]);
      setSessionItems((current) => current.flatMap((item) => {
        const replacement = byId.get(item.id);
        return replacement ? [replacement] : [];
      }));
      setEvidenceRefreshPendingId((pendingId) => {
        const refreshedItem = pendingId ? byId.get(pendingId) : undefined;
        return pendingId && (!refreshedItem || hasCurrentRecurringEvidence(refreshedItem)) ? null : pendingId;
      });
      setCurrentTime(Date.now());
      setFeedLoaded(true);
      setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load discoveries"); }
  }, []);

  const startRefresh = useCallback((): Promise<void> => {
    const currentRefresh = refreshInFlight.current;
    if (currentRefresh) return currentRefresh;
    const pendingRefresh = loadFeed();
    refreshInFlight.current = pendingRefresh;
    void pendingRefresh.finally(() => {
      if (refreshInFlight.current === pendingRefresh) refreshInFlight.current = null;
    });
    return pendingRefresh;
  }, [loadFeed]);

  const refresh = useCallback((force = false): Promise<void> => {
    if (!usesLocalApi()) return Promise.resolve();
    const currentRefresh = refreshInFlight.current;
    return force && currentRefresh
      ? currentRefresh.then(startRefresh)
      : startRefresh();
  }, [startRefresh]);

  const refreshAfterInactivePreview = useCallback(() => {
    if (staleRefreshInFlight.current) return;
    const pendingRefresh = refresh(true);
    staleRefreshInFlight.current = pendingRefresh;
    void pendingRefresh.finally(() => {
      if (staleRefreshInFlight.current === pendingRefresh) staleRefreshInFlight.current = null;
    });
  }, [refresh]);

  const checkTrainingEligibility = useCallback(async (item: DiscoveryItem): Promise<TrainingEligibility> => {
    const response = await fetch(`${API_URL}/api/repertoires/${encodeURIComponent(item.repertoire_id)}` +
      `/opportunities/${encodeURIComponent(item.id)}/training-eligibility`);
    return readJsonResponse(response, discoveryTrainingEligibilitySchema, "discovery training eligibility");
  }, []);

  const recheckTrainingEligibility = useCallback((item: DiscoveryItem) => {
    const key = eligibilityKey(item);
    setTrainingEligibility((items) => {
      const remaining = { ...items };
      delete remaining[key];
      return remaining;
    });
    setEligibilityErrors((items) => {
      const remaining = { ...items };
      delete remaining[key];
      return remaining;
    });
    setError((current) => current === eligibilityErrors[key] ? null : current);
  }, [eligibilityErrors]);

  useEffect(() => {
    if (!active?.card_id) return;
    const key = eligibilityKey(active);
    if (trainingEligibility[key] || eligibilityErrors[key] || eligibilityRequests.current.has(key)) return;
    eligibilityRequests.current.add(key);
    void checkTrainingEligibility(active).then((result) => {
      const current = currentFeedItems.current.get(active.id);
      if (current && eligibilityKey(current) === key)
        setTrainingEligibility((items) => ({ ...items, [key]: result }));
    }).catch((cause) => {
      const current = currentFeedItems.current.get(active.id);
      if (current && eligibilityKey(current) === key) {
        const message = cause instanceof Error ? cause.message : "Could not check training eligibility";
        setEligibilityErrors((items) => ({ ...items, [key]: message }));
        setError(message);
      }
    }).finally(() => { eligibilityRequests.current.delete(key); });
  }, [active, trainingEligibility, eligibilityErrors, checkTrainingEligibility]);

  useEffect(() => {
    if (!usesLocalApi()) return;
    const initialTimer = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(() => void refresh(), 30_000);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(interval); };
  }, [refresh]);

  const loadPreview = useCallback(async (item: DiscoveryItem): Promise<void> => {
    const key = `${item.id}:${item.evidence_fingerprint}`;
    const requestGeneration = previewGenerations.current.get(item.id);
    const isCurrent = () => currentFeedItems.current.get(item.id)?.evidence_fingerprint === item.evidence_fingerprint &&
      previewGenerations.current.get(item.id) === requestGeneration;
    if (requestedPreflights.current.has(key) ||
        (stalePreviewKeys.current.has(key) &&
          performance.now() < (nextPreflightRetryAt.current.get(key) ?? 0)) || !isCurrent()) return;
    requestedPreflights.current.add(key);
    try {
      const response = await backgroundFetch(`${API_URL}/api/discoveries/${item.id}/recommendations`);
      if (!isCurrent()) return;
      if (await inactiveDiscoveryPreview(response)) {
        if (isCurrent()) {
          stalePreviewKeys.current.add(key);
          setPreviews((current) => { const next = { ...current }; delete next[item.id]; return next; });
          setPreviewFingerprints((current) => { const next = { ...current }; delete next[item.id]; return next; });
          setPreviewStatuses((current) => { const next = { ...current }; delete next[item.id]; return next; });
          nextPreflightRetryAt.current.set(key, performance.now() + previewRetryDelayMs);
          refreshAfterInactivePreview();
        }
        return;
      }
      const result = await readJsonResponse(response, discoveryRecommendationSchema, "continuation preview");
      if (!isCurrent()) return;
      stalePreviewKeys.current.delete(key);
      const unusableReadyResult = result.state === "ready" &&
        (result.evidence_fingerprint !== item.evidence_fingerprint || !previewMatchesDecision(item, result));
      const checkedResult: Recommendation = unusableReadyResult
        ? { state: "unavailable", opportunity_id: item.id, candidates: [],
            reason: "Discovery position and recommendation disagree; refresh evidence or inspect it in Builder" }
        : result;
      setPreviews((current) => ({ ...current, [item.id]: checkedResult }));
      setPreviewFingerprints((current) => ({ ...current, [item.id]: item.evidence_fingerprint }));
      setPreviewStatuses((current) => {
        const remaining = { ...current };
        delete remaining[item.id];
        const status = checkedResult.state === "waiting" || checkedResult.state === "unavailable"
          ? checkedResult.state : null;
        return status ? { ...remaining, [item.id]: status } : remaining;
      });
      if (checkedResult.state === "waiting")
        nextPreflightRetryAt.current.set(key, performance.now() + previewRetryDelayMs);
      else nextPreflightRetryAt.current.delete(key);
    } catch (cause) {
      if (!isCurrent()) return;
      stalePreviewKeys.current.delete(key);
      reportDebugError(cause, { kind: "api", source: "discovery preview",
        endpoint: `${API_URL}/api/discoveries/${item.id}/recommendations` });
      setPreviewFingerprints((current) => ({ ...current, [item.id]: item.evidence_fingerprint }));
      setPreviewStatuses((current) => ({ ...current, [item.id]: "failed" }));
      nextPreflightRetryAt.current.set(key, performance.now() + previewRetryDelayMs);
    } finally {
      requestedPreflights.current.delete(key);
      const replacement = currentFeedItems.current.get(item.id);
      if (replacement && !replacement.card_id &&
          replacement.evidence_fingerprint === item.evidence_fingerprint &&
          previewGenerations.current.get(item.id) !== requestGeneration)
        setDiscoveries((current) => [...current]);
    }
  }, [refreshAfterInactivePreview]);

  useEffect(() => {
    if (!feedLoaded || !initialAdmissionFlushFinished) return;
    const preflightItems = discoveries.filter((item) => !item.card_id &&
      !stalePreviewKeys.current.has(`${item.id}:${item.evidence_fingerprint}`) &&
      (previewFingerprints[item.id] !== item.evidence_fingerprint ||
        (!previews[item.id] && !previewStatuses[item.id])));
    if (!initialPreflightComplete) {
      if (initialPreflightStarted.current) return;
      initialPreflightStarted.current = true;
      void withConcurrency(preflightItems, previewConcurrency, loadPreview).finally(() => setInitialPreflightComplete(true));
    } else if (preflightItems.length) {
      void withConcurrency(preflightItems, previewConcurrency, loadPreview);
    }
  }, [feedLoaded, initialAdmissionFlushFinished, discoveries, previewFingerprints, previews, previewStatuses,
    initialPreflightComplete, loadPreview]);

  useEffect(() => {
    if (!initialPreflightComplete) return;
    const retryTimer = window.setInterval(() => {
      const { discoveries: currentDiscoveries, previewFingerprints: currentFingerprints,
        previewStatuses: currentStatuses } = preflightState.current;
      const waitingItems = currentDiscoveries.filter((item) => !item.card_id &&
        performance.now() >= (nextPreflightRetryAt.current.get(`${item.id}:${item.evidence_fingerprint}`) ?? 0) &&
        (stalePreviewKeys.current.has(`${item.id}:${item.evidence_fingerprint}`) ||
          (currentFingerprints[item.id] === item.evidence_fingerprint &&
            (currentStatuses[item.id] === "waiting" || currentStatuses[item.id] === "failed"))));
      void withConcurrency(waitingItems, previewConcurrency, loadPreview);
    }, 3_000);
    return () => window.clearInterval(retryTimer);
  }, [initialPreflightComplete, loadPreview]);

  useEffect(() => {
    if (!openRequest || openRequest.token === lastOpenRequestToken.current)
      return;
    lastOpenRequestToken.current = openRequest.token;
    openViewer(openRequest.id);
    void refresh(true);
  }, [openRequest, openViewer, refresh]);

  useEffect(() => {
    const updatePending = () => {
      try {
        setPendingAdmissions(pendingDiscoveryAdmissions());
      } catch (cause) {
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not read pending discovery saves",
        );
      }
    };
    if (!outboxRecoveryStarted.current) {
      outboxRecoveryStarted.current = true;
      try { recoverUnacknowledgedDiscoveryAdmissions(); }
      catch { /* updatePending reports malformed or unavailable browser storage below. */ }
    }
    const onQueued = (event: Event) => {
      const opportunityId = (event as CustomEvent<{ opportunityId: string }>)
        .detail.opportunityId;
      const notificationKey = `discovery-save:${opportunityId}`;
      const priorSaveNotice = notifications().find((record) =>
        record.key === notificationKey && !record.resolvedAt);
      if (priorSaveNotice) resolveNotification(priorSaveNotice.id,
        { severity: "success", message: "Discovery save confirmed." });
      setCompletedAdmissions((current) => current.includes(opportunityId)
        ? current : [...current, opportunityId]);
      void onQueueChanged().catch((cause) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not refresh the training queue",
        ),
      );
      void refresh(true);
    };
    updatePending();
    window.addEventListener(DISCOVERY_ADMISSIONS_CHANGED, updatePending);
    window.addEventListener(DISCOVERY_ADMISSION_QUEUED, onQueued);
    window.addEventListener("storage", updatePending);
    const flush = () =>
      void flushPendingDiscoveryAdmissions().catch((cause) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not check discovery saves",
        ),
      );
    void flushPendingDiscoveryAdmissions()
      .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not check discovery saves"))
      .finally(() => setInitialAdmissionFlushFinished(true));
    const interval = window.setInterval(flush, 3_000);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener(DISCOVERY_ADMISSIONS_CHANGED, updatePending);
      window.removeEventListener(DISCOVERY_ADMISSION_QUEUED, onQueued);
      window.removeEventListener("storage", updatePending);
    };
  }, [onQueueChanged, refresh]);

  useEffect(() => {
    const nextUnread = visibleDiscoveries.find(
      (item) =>
        item.unread &&
        !openedIds.current.has(item.id) &&
        !suppressedIds.current.has(item.id),
    );
    const reachedBreak = safeBreakCounter !== lastSafeBreak.current;
    lastSafeBreak.current = safeBreakCounter;
    if (interactionBlocked || open) {
      pendingSafeBreak.current = false;
      return;
    }
    if (safeToOpen || reachedBreak) pendingSafeBreak.current = true;
    if (!nextUnread || !pendingSafeBreak.current) return;
    pendingSafeBreak.current = false;
    openViewer(nextUnread.id);
  }, [
    visibleDiscoveries,
    safeToOpen,
    safeBreakCounter,
    interactionBlocked,
    open,
    openViewer,
  ]);

  useEffect(() => {
    if (!open || !active?.unread || openedIds.current.has(active.id)) return;
    openedIds.current.add(active.id);
    void applyOpportunityCommand(active.repertoire_id, active.id, "acknowledge")
      .then(() => refresh(true))
      .catch((cause) => {
        openedIds.current.delete(active.id);
        setError(cause instanceof Error ? cause.message : "Could not acknowledge discovery");
      });
  }, [open, active, refresh]);

  useEffect(() => {
    if (!open || !active || hasCurrentRecurringEvidence(active) ||
        requestedEvidenceRefreshes.current.has(active.id)) {
      return;
    }
    requestedEvidenceRefreshes.current.add(active.id);
    setEvidenceRefreshPendingId(active.id);
    void requestOpportunityRefresh(active.repertoire_id)
      .then(async () => {
        await refresh(true);
      })
      .catch((cause) => {
        requestedEvidenceRefreshes.current.delete(active.id);
        setEvidenceRefreshPendingId(null);
        setError(cause instanceof Error ? cause.message : "Could not refresh discovery evidence");
      });
  }, [open, active, evidenceRefreshPendingId, refresh]);

  useEffect(() => {
    if (!evidenceRefreshPendingId || !open) return;
    const interval = window.setInterval(() => void refresh(), 5_000);
    return () => window.clearInterval(interval);
  }, [evidenceRefreshPendingId, open, refresh]);

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
  const suggestedCandidate = preview?.candidates.find(
    (candidate) => candidate.move_uci === preview.suggested_move_uci);
  const otherCandidate = preview?.candidates.find(
    (candidate) => candidate.move_uci !== preview.suggested_move_uci);
  const activeAdmission = active
    ? pendingAdmissions.find((admission) => admission.opportunityId === active.id)
    : undefined;
  useEffect(() => {
    for (const admission of pendingAdmissions) if (admission.error) publishNotification({
      severity: admission.state === "failed" ? "error" : "warning",
      source: "discovery save", key: `discovery-save:${admission.opportunityId}`,
      message: admission.state === "failed"
        ? `Discovery save failed: ${admission.error}`
        : `Discovery save unconfirmed; Tempo will retry. ${admission.error}`,
    });
  }, [pendingAdmissions]);
  const boardHistory = useMemo(
    () =>
      active
        ? discoveryBoardHistory(
            active,
            fen,
            soundSelection?.preview_moves_uci ?? [],
          )
        : { steps: [], decisionIndex: 0 },
    [active, fen, soundSelection],
  );
  const boardCursor =
    active && boardNavigation?.discoveryId === active.id
      ? Math.min(boardNavigation.cursor, boardHistory.steps.length - 1)
      : boardHistory.decisionIndex;
  const boardStep = boardHistory.steps[boardCursor];
  const atDecision = boardCursor === boardHistory.decisionIndex;
  const observedChange = active?.evidence.later_average_change_cp;
  const observedChangeText = typeof observedChange === "number"
    ? `${Math.abs(observedChange)} cp ${observedChange >= 0 ? "worse" : "better"}` : "unavailable";
  const mateOutcomes = Array.isArray(active?.evidence.mate_outcomes)
    ? active.evidence.mate_outcomes as Array<{ game_id?: string; preceding_move_mate?: number | null; third_later_turn_mate?: number | null }>
    : [];
  const arrowMove = hoveredMove ?? selectedMove;
  const displayedArrow = atDecision && arrowMove && legalDecisionMoves.has(arrowMove) ? arrowMove : null;
  const shapes = useMemo<DrawShape[]>(
    () => [
      ...(boardStep?.moveUci
        ? [
            {
              orig: boardStep.moveUci.slice(0, 2) as Key,
              dest: boardStep.moveUci.slice(2, 4) as Key,
              brush: "blue",
            },
          ]
        : []),
      ...(displayedArrow
        ? [
            {
              orig: displayedArrow.slice(0, 2) as Key,
              dest: displayedArrow.slice(2, 4) as Key,
              brush: "green",
            },
          ]
        : []),
    ],
    [boardStep, displayedArrow],
  );

  useEffect(() => {
    if (!open || !active) return;
    const onArrowKey = (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLSelectElement ||
        event.target instanceof HTMLTextAreaElement ||
        (event.target instanceof HTMLElement && event.target.isContentEditable)
      )
        return;
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const change = event.key === "ArrowLeft" ? -1 : 1;
      setBoardNavigation({
        discoveryId: active.id,
        cursor: Math.max(
          0,
          Math.min(boardHistory.steps.length - 1, boardCursor + change),
        ),
      });
    };
    window.addEventListener("keydown", onArrowKey);
    return () => window.removeEventListener("keydown", onArrowKey);
  }, [open, active, boardCursor, boardHistory.steps.length]);

  const unreadCount = visibleDiscoveries.filter((item) => item.unread).length;
  const preflightPending = !initialPreflightComplete || discoveries.some((item) =>
    !item.card_id && previewFingerprints[item.id] === item.evidence_fingerprint &&
    (previewStatuses[item.id] === "waiting" || previewStatuses[item.id] === "failed"));
  const requestedDiscoveryNotReady = activeId !== null && activeIndex < 0;
  const unavailableReason = activeId
    ? previews[activeId]?.reason
    : discoveries.length === 1 ? previews[discoveries[0].id]?.reason : undefined;

  const act = async (item: DiscoveryItem, action: "train" | "snooze" | "dismiss") => {
    setBusyId(item.id);
    try {
      if (action === "train") {
        const currentEligibility = await checkTrainingEligibility(item);
        const key = eligibilityKey(item);
        setTrainingEligibility((items) => ({ ...items, [key]: currentEligibility }));
        if (!currentEligibility.eligible) return;
      }
      await applyOpportunityCommand(item.repertoire_id, item.id, action);
      if (action === "train") await onQueueChanged();
      await refresh(true);
      if (action !== "train") setActiveId(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : `Could not ${action} discovery`); }
    finally { setBusyId(null); }
  };

  const accept = (item: DiscoveryItem, moveUci: string) => {
    try {
      enqueuePendingDiscoveryAdmission({
        opportunityId: item.id,
        selectedMoveUci: moveUci,
        evidenceFingerprint: item.evidence_fingerprint,
      });
      const next = reviewItems
        .slice(activeIndex + 1)
        .find(
          (candidate) =>
            !pendingDiscoveryAdmissions().some(
              (pending) => pending.opportunityId === candidate.id,
            ) &&
            !completedAdmissions.includes(candidate.id) &&
            candidate.admission_state !== "preparing" &&
            candidate.admission_state !== "queued",
        );
      if (next) {
        setHoveredMove(null);
        setActiveId(next.id);
      }
      void flushPendingDiscoveryAdmissions(item.id).catch((cause) =>
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not save the discovery",
        ),
      );
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Could not add continuation",
      );
    }
  };

  return (
    <aside className="tempo-activity-tray tempo-discoveries-tray">
      <Button
        ref={triggerRef}
        type="button"
        className="tempo-activity-trigger"
        aria-label="Discoveries"
        aria-expanded={open}
        onClick={() => {
          if (open) closeViewer();
          else openViewer(visibleDiscoveries[0]?.id ?? null);
        }}
      >
        <span className="tempo-discoveries-label-desktop">Discoveries</span>
        <span className="tempo-discoveries-label-mobile" aria-hidden="true">✦</span>
        {unreadCount > 0 && (
          <span
            className="tempo-discoveries-badge"
            aria-label={`${unreadCount} new discoveries`}
          >
            {" "}
            {unreadCount}
          </span>
        )}
      </Button>
      {open && (
        <div className="tempo-discovery-backdrop">
          <section
            ref={dialogRef}
            className="ui-dialog tempo-discovery-viewer"
            role="dialog"
            aria-modal="true"
            aria-label="Discoveries"
          >
            {pendingAdmissions.filter((admission) => admission.state === "failed" && admission.error).map((admission) =>
              <p key={admission.opportunityId} role="alert">Discovery save failed: {admission.error}{" "}
                <Button type="button" onClick={() => void retryPendingDiscoveryAdmission(admission.opportunityId)}>Retry save</Button>
              </p>)}
            <header className="tempo-discovery-header">
              <div>
                <span className="pill">Discoveries</span>
                <h2>
                  {active
                    ? discoveryTitle(active)
                    : requestedDiscoveryNotReady
                      ? "This discovery is not ready for review"
                      : preflightPending
                        ? "Preparing review-ready discoveries"
                        : "No discoveries ready for review"}
                </h2>
                <p>
                  {active
                    ? `${activeIndex + 1} of ${reviewItems.length} · ${new Chess(fen).turn() === "w" ? "white" : "black"} to move`
                    : requestedDiscoveryNotReady
                      ? unavailableReason ?? "Choose Next to review a complete discovery."
                      : preflightPending
                        ? "Preparing review-ready discoveries."
                        : unavailableReason ?? "There are no complete discoveries to review right now."}
                </p>
              </div>
              <div className="tempo-discovery-navigation">
                <Button
                  type="button"
                  disabled={activeIndex <= 0}
                  onClick={() => setActiveId(reviewItems[activeIndex - 1].id)}
                >
                  Previous
                </Button>
                <Button
                  type="button"
                  disabled={
                    activeIndex < 0 || activeIndex >= reviewItems.length - 1
                  }
                  onClick={() => setActiveId(reviewItems[activeIndex + 1].id)}
                >
                  Next
                </Button>
                <Button ref={closeRef} type="button" onClick={closeViewer}>
                  Back to work
                </Button>
              </div>
            </header>
            {error && (
              <p role="alert">
                {error}{" "}
                <Button
                  onClick={() => {
                    setError(null);
                    setEligibilityErrors({});
                    void refresh(true);
                  }}
                >
                  Retry
                </Button>
              </p>
            )}
            {active && (
              <div className="tempo-discovery-main">
                <div className="tempo-discovery-board">
                  <Chessboard
                    owner="discoveries"
                    fen={boardStep?.fen ?? fen}
                    orientation={active.trained_color}
                    locked
                    showHint={false}
                    lastMove={
                      boardStep?.moveUci
                        ? [
                            boardStep.moveUci.slice(0, 2),
                            boardStep.moveUci.slice(2, 4),
                          ]
                        : undefined
                    }
                    theme={boardTheme}
                    pieceSet={pieceSet}
                    shapes={shapes}
                    onMove={() => undefined}
                  />
                  <div className="tempo-discovery-move-navigation">
                    <Button
                      type="button"
                      aria-label="Previous move"
                      disabled={boardCursor <= 0}
                      onClick={() =>
                        setBoardNavigation({
                          discoveryId: active.id,
                          cursor: boardCursor - 1,
                        })
                      }
                    >
                      ◀
                    </Button>
                    <span>
                      {boardStep?.moveSan ?? "Start"}
                      {atDecision ? " · decision" : ""}
                    </span>
                    <Button
                      type="button"
                      aria-label="Next move"
                      disabled={boardCursor >= boardHistory.steps.length - 1}
                      onClick={() =>
                        setBoardNavigation({
                          discoveryId: active.id,
                          cursor: boardCursor + 1,
                        })
                      }
                    >
                      ▶
                    </Button>
                  </div>
                  <p role="status">
                    {displayedArrow
                      ? `${moveSan(fen, displayedArrow)} selected. The green arrow shows its destination.`
                      : boardStep?.moveUci
                        ? "The blue arrow shows the previous move."
                        : "Select a move in the table to see it on the board."}
                  </p>
                  <div className="tempo-discovery-actions">
                    {active.card_id && (
                      <Button
                        disabled={
                          busyId === active.id ||
                          active.admission_state === "queued" ||
                          !trainingEligibility[eligibilityKey(active)]?.eligible
                        }
                        onClick={() => void act(active, "train")}
                      >
                        {active.admission_state === "queued"
                          ? "In training queue"
                          : "Train this decision"}
                      </Button>
                    )}
                    {active.card_id && !trainingEligibility[eligibilityKey(active)]?.eligible && (
                      <p role="status">{trainingEligibility[eligibilityKey(active)]?.reason ??
                        eligibilityErrors[eligibilityKey(active)] ?? "Checking direct training eligibility."}
                        {" "}Open in Builder to inspect this decision.
                        {(eligibilityErrors[eligibilityKey(active)] ||
                          trainingEligibility[eligibilityKey(active)]?.eligible === false) && (
                          <> <Button type="button" disabled={busyId === active.id}
                            onClick={() => recheckTrainingEligibility(active)}>
                            {eligibilityErrors[eligibilityKey(active)] ? "Retry eligibility" : "Recheck eligibility"}
                          </Button></>
                        )}
                      </p>
                    )}
                    {!active.card_id && (
                      <Button
                        disabled={
                          !soundSelection ||
                          busyId === active.id ||
                          active.admission_state === "preparing" ||
                          active.admission_state === "queued" ||
                          pendingAdmissions.some(
                            (item) => item.opportunityId === active.id,
                          ) ||
                          completedAdmissions.includes(active.id)
                        }
                        onClick={() => {
                          if (soundSelection)
                            accept(active, soundSelection.move_uci);
                        }}
                      >
                        Add and train
                      </Button>
                    )}
                    <Button
                      onClick={() => {
                        closeViewer();
                        if (onOpenBuilder) onOpenBuilder(active, selectedMove);
                        else onOpenRepertoire?.();
                      }}
                    >
                      Open in Builder
                    </Button>
                  </div>
                  {active.admission_state === "preparing" && (
                    <p role="status">
                      Preparing training card. Tempo is publishing and checking
                      the repertoire branch.
                    </p>
                  )}
                  {activeAdmission && (
                    <p role="status">
                      {activeAdmission.state === "failed"
                        ? `Save failed: ${activeAdmission.error ?? "Retry the save."}`
                        : activeAdmission.error
                          ? `Save unconfirmed; retrying. ${activeAdmission.error}`
                          : "Save pending. Tempo will confirm when this card reaches the training queue."}
                    </p>
                  )}
                  {!active.card_id && selectedMove && !soundSelection && (
                    <p role="status">
                      This move needs engine validation before Add and train is
                      available. You can investigate it in Builder.
                    </p>
                  )}
                  {soundSelection && (
                    <p>
                      Preview: {notation(fen, soundSelection.preview_moves_uci)}{" "}
                      · {soundSelection.similarity}
                      {soundSelection.example_line_name
                        ? ` in ${soundSelection.example_line_name}`
                        : ""}
                    </p>
                  )}
                </div>
                <div className="tempo-discovery-detail">
                  <p className="tempo-discovery-summary">
                    {active.evidence.analysis_based
                      ? `Past ${evidenceNumber(active, "window_days")} days: ${evidenceNumber(active, "encounter_count")} encounters, ${evidenceNumber(active, "miss_count")} confirmed mistakes in ${evidenceNumber(active, "analyzed_count")} analyzed decisions.`
                      : `${evidenceNumber(active, "supporting_games")} supporting games. ${acceptedMoves.length ? "A continuation is saved." : "This response is missing from your repertoire."}`}
                  </p>
                  {active.kind === "missing_response" &&
                    active.opponent_move_uci && (
                      <p>
                        After the opponent plays{" "}
                        <strong>
                          {moveSan(active.fen, active.opponent_move_uci)}
                        </strong>
                        , this is your decision.
                      </p>
                    )}
                  {active.routes[0] && (
                    <p>
                      Example route: {active.routes[0]}
                      {active.routes.length > 1
                        ? ` · ${active.routes.length} distinct routes reach this position`
                        : ""}
                    </p>
                  )}
                  <p className="source-status" role="status">
                    Stockfish:{" "}
                    {active.card_id
                      ? engineStatus
                      : preview?.state === "ready"
                        ? "Ready"
                        : (preview?.reason ?? "Preparing")}{" "}
                    · Lichess:{" "}
                    {explorer?.lichess.message ??
                      explorer?.lichess.state ??
                      "Loading"}{" "}
                    · Masters:{" "}
                    {explorer?.masters.message ??
                      explorer?.masters.state ??
                      "Loading"}
                  </p>
                  {evidenceRefreshPendingId === active.id && (
                    <p role="status">
                      Refreshing older analysis evidence. This will update when
                      the background refresh finishes.
                    </p>
                  )}
                  <p className="panel-message">
                    Stockfish grades move quality. Explorer counts show what
                    people played; popularity does not establish that a move is
                    sound.
                  </p>
                  {!active.card_id && preview?.state === "ready" && preview.candidates.length > 0 && (
                    <section className="tempo-discovery-candidate-comparison" aria-label="Discovery move comparison">
                      <h3>Compare eligible moves</h3>
                      <p>Tempo suggests a familiar eligible move first, then the one closest to the engine best move. Familiar moves may be up to 100 cp from best; unfamiliar moves must be within 30 cp.</p>
                      {suggestedCandidate && otherCandidate && <p>
                        Compared with {moveSan(fen, otherCandidate.move_uci)}, the suggestion appears in {suggestedCandidate.repertoire_line_count} versus {otherCandidate.repertoire_line_count} comparable repertoire lines.
                        {suggestedCandidate.loss_cp !== null && otherCandidate.loss_cp !== null
                          ? ` Their engine gaps are ${suggestedCandidate.loss_cp} versus ${otherCandidate.loss_cp} cp from best.`
                          : " A mate line has no centipawn gap for comparison."}
                      </p>}
                      <ul>
                        {preview.candidates.map((candidate) => {
                          const isSuggested = candidate.move_uci === preview.suggested_move_uci;
                          const lineCount = candidate.repertoire_line_count;
                          return <li key={candidate.move_uci}>
                            <Button type="button" aria-pressed={selectedMove === candidate.move_uci}
                              onClick={() => {
                                setManualSelections((current) => ({ ...current, [active.id]: candidate.move_uci }));
                                setBoardNavigation({ discoveryId: active.id, cursor: boardHistory.decisionIndex });
                              }}>
                              {isSuggested ? "Suggested " : "Choose "}{moveSan(fen, candidate.move_uci)}
                            </Button>
                            <span>{lineCount} comparable repertoire {lineCount === 1 ? "line" : "lines"}</span>
                            <span>{candidate.loss_cp === null ? "Mate line; no cp gap" : `${candidate.loss_cp} cp from best`}</span>
                            <span>{candidate.exact_transposition ? "Exact transposition" : candidate.similarity}</span>
                            {candidate.example_line_name && <span>Example: {candidate.example_line_name}</span>}
                          </li>;
                        })}
                      </ul>
                    </section>
                  )}
                  <MoveComparisonTable
                    mode="discovery"
                    repertoire={repertoireMoves}
                    engine={engineMoves}
                    engineLossCp={engineLossCp}
                    lichess={explorerMoves}
                    masters={mastersMoves}
                    maia={[]}
                    turn={active.trained_color}
                    selectedMove={selectedMove}
                    onPlay={(moveUci) => {
                      setManualSelections((current) => ({
                        ...current,
                        [active.id]: moveUci,
                      }));
                      setBoardNavigation({
                        discoveryId: active.id,
                        cursor: boardHistory.decisionIndex,
                      });
                    }}
                    onHover={setHoveredMove}
                  />
                  <details className="tempo-discovery-evidence">
                    <summary>Why this position was flagged</summary>
                    <p>
                      Analysis coverage:{" "}
                      {evidenceNumber(active, "analyzed_count")} of{" "}
                      {evidenceNumber(active, "encounter_count")} encounters.
                      Strong prefix:{" "}
                      {evidenceNumber(active, "strong_prefix_count")} of{" "}
                      {evidenceNumber(active, "sufficient_prefix_count")}{" "}
                      sufficiently analyzed routes.
                    </p>
                    <p>
                      Immediate loss:{" "}
                      {active.evidence.immediate_cp_sample_count === 0
                        ? "no complete samples"
                        : `${evidenceNumber(active, "immediate_average_loss_cp")} cp over ${evidenceNumber(active, "immediate_cp_sample_count")} complete samples`}
                      . Observed change through your third later turn:{" "}
                      {active.evidence.later_sample_count === 0
                        ? "no complete games"
                        : `${observedChangeText} over ${evidenceNumber(active, "later_sample_count")} complete games`}
                      . The later change is an observed outcome, not solely the
                      result of one move.
                    </p>
                    {mateOutcomes.length > 0 && (
                      <ul>
                        {mateOutcomes.map((outcome, index) => (
                          <li key={`${outcome.game_id ?? "game"}-${index}`}>
                            {outcome.game_id ?? "Game"}: after the preceding
                            move{" "}
                            {outcome.preceding_move_mate === null ||
                            outcome.preceding_move_mate === undefined
                              ? "no mate score"
                              : `mate ${outcome.preceding_move_mate}`}
                            ; after your third later turn{" "}
                            {outcome.third_later_turn_mate === null ||
                            outcome.third_later_turn_mate === undefined
                              ? "no mate score"
                              : `mate ${outcome.third_later_turn_mate}`}
                          </li>
                        ))}
                      </ul>
                    )}
                    {active.source_games.length > 0 && (
                      <ul>
                        {active.source_games.map((game) => (
                          <li key={game.id}>
                            {game.url ? (
                              <a
                                href={game.url}
                                target="_blank"
                                rel="noreferrer"
                              >
                                {game.route || game.id}
                              </a>
                            ) : (
                              game.route || game.id
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </details>
                  <div className="tempo-discovery-secondary-actions">
                    <Button
                      disabled={busyId === active.id}
                      onClick={() => void act(active, "snooze")}
                    >
                      Snooze 7 days
                    </Button>
                    <Button
                      disabled={busyId === active.id}
                      onClick={() => void act(active, "dismiss")}
                    >
                      Dismiss
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </aside>
  );
}
