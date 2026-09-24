"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Chess } from "chess.js";
import { API_URL } from "../const";
import { discoveriesFeedSchema, discoveryRecommendationSchema } from "../domain/schemas";
import { backgroundFetch } from "../lib/background-fetch";
import { readJsonResponse } from "../lib/validated-data";
import { usesLocalApi } from "../utils/local";
import type { z } from "zod";

type Discovery = z.infer<typeof discoveriesFeedSchema>["discoveries"][number];
type Recommendation = z.infer<typeof discoveryRecommendationSchema>;

function continuationNotation(startingFen: string, moves: string[]): string {
  try {
    const board = new Chess(startingFen);
    return moves.map((uci) => board.move({ from: uci.slice(0, 2), to: uci.slice(2, 4),
      promotion: uci[4] })?.san ?? uci).join(" ");
  } catch {
    return moves.join(" ");
  }
}

function numberEvidence(discovery: Discovery, key: string): string {
  const value = discovery.evidence[key];
  return typeof value === "number" ? String(value) : "unavailable";
}

export function DiscoveriesTray({ safeToOpen, safeBreakCounter, interactionBlocked = false, onOpenRepertoire, onQueueChanged }: {
  safeToOpen: boolean;
  safeBreakCounter: number;
  interactionBlocked?: boolean;
  onOpenRepertoire: () => void;
  onQueueChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [discoveries, setDiscoveries] = useState<Discovery[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [previews, setPreviews] = useState<Record<string, Recommendation>>({});
  const [currentTime, setCurrentTime] = useState(() => Date.now());
  const openedIds = useRef(new Set<string>());
  const lastSafeBreak = useRef(safeBreakCounter);
  const loadedPageCount = useRef(1);

  const refresh = useCallback(async () => {
    if (!usesLocalApi()) return;
    try {
      const pages: Array<z.infer<typeof discoveriesFeedSchema>> = [];
      let offset: number | null = 0;
      for (let pageIndex = 0; pageIndex < loadedPageCount.current && offset !== null; pageIndex++) {
        const response = await backgroundFetch(`${API_URL}/api/discoveries?offset=${offset}&limit=100`);
        const result = await readJsonResponse(response, discoveriesFeedSchema, "discoveries");
        pages.push(result);
        offset = result.next_offset;
      }
      const unique = new Map(pages.flatMap((page) => page.discoveries).map((item) => [item.id, item]));
      setDiscoveries([...unique.values()]);
      setUnreadCount(pages[0]?.unread_count ?? 0);
      setNextOffset(offset);
      setCurrentTime(Date.now());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load discoveries");
    }
  }, []);

  const loadMore = async () => {
    if (nextOffset === null) return;
    try {
      const response = await backgroundFetch(`${API_URL}/api/discoveries?offset=${nextOffset}&limit=100`);
      const result = await readJsonResponse(response, discoveriesFeedSchema, "discoveries");
      setDiscoveries((current) => {
        const existingIds = new Set(current.map((item) => item.id));
        return [...current, ...result.discoveries.filter((item) => !existingIds.has(item.id))];
      });
      setNextOffset(result.next_offset);
      loadedPageCount.current += 1;
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load more discoveries");
    }
  };

  useEffect(() => {
    if (!usesLocalApi()) return;
    const initialTimer = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5_000);
    return () => { window.clearTimeout(initialTimer); window.clearInterval(timer); };
  }, [refresh]);

  const acknowledgeVisible = useCallback(async (items: Discovery[]) => {
    const newlyOpened = items.filter((item) => item.unread && !openedIds.current.has(item.id));
    for (const item of newlyOpened) openedIds.current.add(item.id);
    await Promise.all(newlyOpened.map(async (item) => {
      const response = await fetch(`${API_URL}/api/repertoires/${item.repertoire_id}/opportunities/${item.id}/acknowledge`, { method: "POST" });
      if (!response.ok) throw new Error(`Could not acknowledge discovery: HTTP ${response.status}`);
    }));
    if (newlyOpened.length) await refresh();
  }, [refresh]);

  useEffect(() => {
    const newUnread = discoveries.some((item) => item.unread && !openedIds.current.has(item.id)
      && (!item.snoozed_until || new Date(item.snoozed_until).getTime() <= currentTime));
    const reachedBreak = safeBreakCounter !== lastSafeBreak.current;
    lastSafeBreak.current = safeBreakCounter;
    if (!newUnread) return;
    if (interactionBlocked) return;
    if (open) {
      void acknowledgeVisible(discoveries).catch((cause) => setError(cause instanceof Error ? cause.message : "Could not acknowledge discovery"));
      return;
    }
    if (!safeToOpen && !reachedBreak) return;
    setOpen(true);
    void acknowledgeVisible(discoveries).catch((cause) => setError(cause instanceof Error ? cause.message : "Could not acknowledge discovery"));
  }, [discoveries, safeToOpen, safeBreakCounter, interactionBlocked, open, acknowledgeVisible, currentTime]);

  const act = async (item: Discovery, action: "train" | "snooze" | "dismiss") => {
    setBusyId(item.id);
    try {
      const response = await fetch(`${API_URL}/api/repertoires/${item.repertoire_id}/opportunities/${item.id}/${action}`, { method: "POST" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `${action} failed: HTTP ${response.status}`);
      }
      if (action === "train") await onQueueChanged();
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not ${action} discovery`);
    } finally {
      setBusyId(null);
    }
  };

  const loadPreview = async (item: Discovery) => {
    setBusyId(item.id);
    try {
      const response = await fetch(`${API_URL}/api/discoveries/${item.id}/recommendations`);
      const preview = await readJsonResponse(response, discoveryRecommendationSchema, "continuation preview");
      setPreviews((current) => ({ ...current, [item.id]: preview }));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not prepare continuation");
    } finally { setBusyId(null); }
  };

  useEffect(() => {
    if (!open) return;
    const pendingIds = Object.entries(previews)
      .filter(([, preview]) => preview.state === "waiting")
      .map(([id]) => id);
    if (!pendingIds.length) return;
    const timer = window.setInterval(() => {
      for (const id of pendingIds) {
        void backgroundFetch(`${API_URL}/api/discoveries/${id}/recommendations`)
          .then((response) => readJsonResponse(response, discoveryRecommendationSchema, "continuation preview"))
          .then((preview) => setPreviews((current) => ({ ...current, [id]: preview })))
          .catch((cause) => setError(cause instanceof Error ? cause.message : "Could not refresh continuation"));
      }
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [open, previews]);

  const acceptPreview = async (item: Discovery, moveUci: string) => {
    setBusyId(item.id);
    try {
      const response = await fetch(`${API_URL}/api/discoveries/${item.id}/accept`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_move_uci: moveUci,
          evidence_fingerprint: item.evidence_fingerprint }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `Could not add continuation (HTTP ${response.status})`);
      }
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not add continuation");
    } finally { setBusyId(null); }
  };

  return <aside className="tempo-activity-tray tempo-discoveries-tray">
    <button type="button" className="tempo-activity-trigger" aria-label="Discoveries" aria-expanded={open}
      aria-controls="tempo-discoveries-content" onClick={() => {
        setOpen((value) => !value);
        if (!open) void acknowledgeVisible(discoveries).catch((cause) => setError(cause instanceof Error ? cause.message : "Could not acknowledge discovery"));
      }}>
      Discoveries{unreadCount > 0 && <span className="tempo-discoveries-badge" aria-label={`${unreadCount} new discoveries`}> {unreadCount}</span>}
    </button>
    {open && <section id="tempo-discoveries-content" className="tempo-activity-content" aria-label="Discoveries">
      <div className="tempo-activity-heading"><strong>Discoveries</strong><button type="button" onClick={() => setOpen(false)}>Close</button></div>
      {error && <p role="alert">{error} <button type="button" onClick={() => void refresh()}>Retry</button></p>}
      {!error && discoveries.length === 0 && <p>No current discoveries.</p>}
      <div className="tempo-activity-list">
        {discoveries.filter((item) => !item.snoozed_until || new Date(item.snoozed_until).getTime() <= currentTime).map((item) => {
          const analyzed = Boolean(item.evidence.analysis_based);
          const hasCard = Boolean(item.card_id);
          const route = item.routes[0];
          const observedChange = item.evidence.later_average_change_cp;
          const observedChangeText = typeof observedChange === "number"
            ? `${Math.abs(observedChange)} cp ${observedChange >= 0 ? "worse" : "better"}`
            : "unavailable";
          const mateOutcomes = Array.isArray(item.evidence.mate_outcomes)
            ? item.evidence.mate_outcomes as Array<{ game_id?: string; preceding_move_mate?: number | null; third_later_turn_mate?: number | null }>
            : [];
          return <article key={item.id} className="tempo-activity-item">
            <strong>{analyzed ? item.evidence.strong_prefix_qualifies ? "Strong opening, weak next decision" : "Recurring weak decision" : item.kind === "weak_known_decision" ? "Opening decision to practice" : "Repertoire continuation to investigate"}</strong>
            {route && <p>Game route: {route}{item.routes.length > 1 ? ` · ${item.routes.length} distinct routes` : ""}</p>}
            <details><summary>Decision position</summary><code>{item.fen}</code></details>
            {analyzed ? <p>Past {numberEvidence(item, "window_days")} days: {numberEvidence(item, "encounter_count")} encounters, {numberEvidence(item, "strong_prefix_count")} strong prefixes among {numberEvidence(item, "sufficient_prefix_count")} sufficiently analyzed routes, {numberEvidence(item, "miss_count")} engine-confirmed mistakes in {numberEvidence(item, "analyzed_count")} analyzed decisions. Immediate loss averages {numberEvidence(item, "immediate_average_loss_cp")} cp over {numberEvidence(item, "immediate_cp_sample_count")} centipawn samples. Observed change through your third later turn averages {observedChangeText} across {numberEvidence(item, "later_sample_count")} complete games.{mateOutcomes.length ? ` ${mateOutcomes.length} mate outcome${mateOutcomes.length === 1 ? "" : "s"} shown separately.` : ""}</p>
              : <p>{numberEvidence(item, "supporting_games")} supporting games. {hasCard ? "A continuation is saved in your repertoire." : "This continuation is missing from your repertoire."}</p>}
            {item.source_games.length > 0 && <details><summary>Supporting games</summary><ul>{item.source_games.map((game) => <li key={game.id}>{game.url ? <a href={game.url} target="_blank" rel="noreferrer">{game.route || game.id}</a> : <span>{game.route || game.id}</span>}</li>)}</ul></details>}
            {mateOutcomes.length > 0 && <details><summary>Mate outcomes ({mateOutcomes.length})</summary><ul>{mateOutcomes.map((outcome, index) => <li key={`${outcome.game_id ?? "game"}-${index}`}>{outcome.game_id ?? "Game"}: after preceding move {outcome.preceding_move_mate === null || outcome.preceding_move_mate === undefined ? "no mate score" : `mate ${outcome.preceding_move_mate}`}; after third later turn {outcome.third_later_turn_mate === null || outcome.third_later_turn_mate === undefined ? "no mate score" : `mate ${outcome.third_later_turn_mate}`}</li>)}</ul></details>}
            <div className="tempo-activity-actions">
              {hasCard && <button type="button" disabled={busyId === item.id || item.admission_state === "queued"} onClick={() => void act(item, "train")}>{item.admission_state === "queued" ? "In training queue" : "Train this decision"}</button>}
              {!hasCard && !item.admission_state && <button type="button" disabled={busyId === item.id} onClick={() => void loadPreview(item)}>Preview continuation</button>}
              {!hasCard && <button type="button" onClick={onOpenRepertoire}>Investigate repertoire</button>}
              <button type="button" disabled={busyId === item.id} onClick={() => void act(item, "snooze")}>Snooze 7 days</button>
              <button type="button" disabled={busyId === item.id} onClick={() => void act(item, "dismiss")}>Dismiss</button>
            </div>
            {item.admission_state === "preparing" && <p role="status">Preparing training card. Tempo is publishing and checking the repertoire branch.</p>}
            {previews[item.id]?.state === "waiting" && <p role="status">{previews[item.id].reason}</p>}
            {previews[item.id]?.state === "ready" && <div className="discovery-preview">
              {previews[item.id].accepted_moves_uci?.length ? <p>Saved continuation: {previews[item.id].accepted_moves_uci?.join(", ")}</p> : <p>No accepted continuation at this decision is in the full repertoire.</p>}
              {previews[item.id].candidates.map((candidate) => <div key={candidate.move_uci}>
                <p><strong>{continuationNotation(previews[item.id].starting_fen ?? item.fen, [candidate.move_uci])}</strong> · {candidate.loss_cp === null ? candidate.score.mate === null ? "typed score unavailable" : `mate ${candidate.score.mate}` : `${candidate.loss_cp} cp from best`} · {candidate.similarity}{candidate.example_line_name ? ` (${candidate.example_line_name})` : ""}</p>
                <p>Preview: {continuationNotation(previews[item.id].starting_fen ?? item.fen, candidate.preview_moves_uci)}</p>
                <button type="button" disabled={busyId === item.id || item.admission_state === "preparing"} onClick={() => void acceptPreview(item, candidate.move_uci)}>Add and train</button>
              </div>)}
            </div>}
          </article>;
        })}
      </div>
      {nextOffset !== null && <button type="button" onClick={() => void loadMore()}>Load more discoveries</button>}
    </section>}
  </aside>;
}
