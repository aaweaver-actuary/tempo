"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Board } from "@tempo/board-ui";
// The package's exported stylesheet; root package registration belongs to #491.
import "../../../packages/board-ui/styles/board.css";
import { Button } from "../../components/buttons/BaseButton";
import type { Finding, GameDetail, GameSummary, RecentGames } from "../product-api/contracts";
import type { GameReviewClient } from "./api";
import { boardPosition, departureLabels, departureMove, departureProgress, departureProgressStage, departureReceipt, findingLabels, gamePositions, isEncounterReceipt, OperationKeys, OwnedRequests, receiptLabel } from "./state";
import "./game-review.css";
import { useGameRefresh } from "./use-game-refresh";

export type GapReviewIntent = Pick<Finding, "provider" | "game_id" | "source_revision" | "repertoire_id" | "repertoire_revision" | "position_key" | "ply"> & { kind: "review_repertoire_gap" };
export interface GameReviewViewProps {
  api: GameReviewClient;
  repertoireId?: string;
  onReviewGap?: (intent: GapReviewIntent) => void;
}
type Load<T> = { data: T | null; loading: boolean; error: string | null };
const emptyLoad = <T,>(): Load<T> => ({ data: null, loading: false, error: null });
const providerLabel = (provider: GameSummary["provider"]) => provider === "lichess" ? "Lichess" : "Chess.com";
const gameKey = (game: GameSummary) => JSON.stringify([game.provider, game.game_id, game.source_revision]);
const findingKey = (finding: Finding) => JSON.stringify([finding.provider, finding.game_id, finding.source_revision, finding.repertoire_id, finding.repertoire_revision, finding.ply, finding.decision_id]);
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Game Review is unavailable. Retry is available.";

function gameTime(game: GameSummary): string {
  if (!game.played_at || game.time_precision === "unknown") return "Date and time unknown";
  const iso = new Date(game.played_at).toISOString();
  if (game.time_precision === "day") return `${iso.slice(0, 10)} (day precision; time unknown)`;
  const length = game.time_precision === "hour" ? 13 : game.time_precision === "minute" ? 16 : game.time_precision === "second" ? 19 : 23;
  return `${iso.slice(0, length).replace("T", " ")} UTC (${game.time_precision} precision)`;
}

function receiptFor(receipts: GameDetail["receipts"], finding: Finding) {
  return receipts.find(receipt => receipt.key.provider === finding.provider && receipt.key.game_id === finding.game_id
    && receipt.key.occurrence_ply === finding.ply && receipt.key.decision_id === finding.decision_id && receipt.source_revision === finding.source_revision);
}

export function GameReviewView({ api, repertoireId, onReviewGap }: GameReviewViewProps) {
  const requests = useRef(new OwnedRequests());
  const operations = useRef(new OperationKeys());
  const feedbackBusy = useRef(false);
  const [recent, setRecent] = useState<Load<RecentGames>>({ ...emptyLoad<RecentGames>(), loading: true });
  const [selected, setSelected] = useState<GameSummary | null>(null);
  const [detail, setDetail] = useState<Load<GameDetail> & { identity?: string }>(emptyLoad);
  const [reload, setReload] = useState(0);
  const [detailReload, setDetailReload] = useState(0);
  const refresh = useGameRefresh(api, () => setReload(value => value + 1));
  const [ply, setPly] = useState(0);
  const [feedback, setFeedback] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  const selectionIdentity = selected ? JSON.stringify([gameKey(selected), repertoireId ?? null]) : "";

  useEffect(() => {
    const owner = requests.current;
    return () => owner.cancelAll();
  }, []);

  useEffect(() => {
    const owner = requests.current;
    const owned = owner.start("recent");
    void Promise.resolve().then(() => {
      if (!owned.current()) return;
      setRecent(previous => ({ ...previous, loading: true, error: null }));
      return api.recentGames({ signal: owned.signal });
    }).then(data => {
      if (!data || !owned.current()) return;
      setRecent({ data, loading: false, error: null });
      setSelected(previous => {
        if (!previous) return null;
        const current = data.games.find(game => game.provider === previous.provider && game.game_id === previous.game_id);
        return current && gameKey(current) === gameKey(previous) ? previous : current ?? null;
      });
      for (const status of data.sync) if (status.status === "complete") operations.current.complete(`sync:${status.provider}`);
    }).catch(error => {
      if (owned.current()) setRecent(previous => ({ ...previous, loading: false, error: errorMessage(error) }));
    });
    return () => owner.cancel("recent");
  }, [api, reload]);

  useEffect(() => {
    if (!selected) return;
    const owner = requests.current;
    const owned = owner.start("detail");
    void Promise.resolve().then(() => {
      if (!owned.current()) return;
      feedbackBusy.current = false;
      setFeedback({ busy: false, error: null });
      setDetail({ data: null, loading: true, error: null, identity: selectionIdentity });
      setPly(0);
      return api.gameDetail({ provider: selected.provider, game_id: selected.game_id, repertoire_id: repertoireId, signal: owned.signal });
    }).then(data => {
      if (!data || !owned.current()) return;
      try {
        gamePositions(data.starting_fen, data.moves);
        if (gameKey(data) !== gameKey(selected)) throw new Error("Game identity or source revision changed. Refresh recent games before reviewing.");
        if (data.findings.some(finding => finding.provider !== data.provider || finding.game_id !== data.game_id
          || finding.ply > data.moves.length || data.moves[finding.ply - 1] !== finding.played_uci
          || repertoireId !== undefined && finding.repertoire_id !== repertoireId)) throw new Error("Findings do not match this game history or repertoire. Refresh before reviewing.");
        setDetail({ data, loading: false, error: null, identity: selectionIdentity });
        setPly(data.first_departure?.ply ? data.first_departure.ply - 1 : 0);
      } catch (error) {
        setDetail({ data: null, loading: false, error: errorMessage(error), identity: selectionIdentity });
      }
    }).catch(error => {
      if (owned.current()) setDetail({ data: null, loading: false, error: errorMessage(error), identity: selectionIdentity });
    });
    return () => { owner.cancel("detail"); owner.cancel("feedback"); };
  }, [api, selected, repertoireId, selectionIdentity, detailReload]);

  const currentDetail = detail.identity === selectionIdentity ? detail.data : null;
  const positions = useMemo(() => currentDetail ? gamePositions(currentDetail.starting_fen, currentDetail.moves) : [], [currentDetail]);
  const cursor = Math.max(0, Math.min(ply, positions.length - 1));
  const selection = currentDetail?.first_departure;
  const primaryReceipt = currentDetail ? departureReceipt(currentDetail) : undefined;
  const primaryProgress = isEncounterReceipt(primaryReceipt) ? primaryReceipt.progress : undefined;

  function matchesSelectedDeparture(finding: Finding): boolean {
    const selected = currentDetail?.first_departure;
    return selected?.status === "own_actionable" && selected.decision !== null
      && finding.provider === selected.source.provider && finding.game_id === selected.source.game_id
      && finding.source_revision === selected.source.source_revision && finding.source_digest === selected.source.source_digest
      && finding.ply === selected.ply && finding.decision_id === selected.decision.decision_id
      && finding.position_key === selected.position_key && finding.played_uci === selected.played_uci
      && finding.expected_uci.length === selected.expected_uci.length
      && finding.expected_uci.every((move, index) => move === selected.expected_uci[index]);
  }

  async function retryFeedback(finding: Finding) {
    if (feedbackBusy.current || finding.kind !== "confirmed_miss" || !matchesSelectedDeparture(finding)) return;
    feedbackBusy.current = true;
    setFeedback({ busy: true, error: null });
    const owned = requests.current.start("feedback");
    const operation_id = operations.current.get(`feedback:${findingKey(finding)}`);
    try {
      const receipt = await api.retryFeedback({ finding, operation_id, signal: owned.signal });
      if (!owned.current()) return;
      if (!receiptFor([receipt], finding)) throw new Error("Feedback receipt does not match the original game finding.");
      setDetail(previous => previous.identity !== selectionIdentity || !previous.data ? previous : {
        ...previous, data: { ...previous.data, receipts: [...previous.data.receipts.filter(item => !receiptFor([item], finding)), receipt] },
      });
      setFeedback({ busy: false, error: null });
    } catch (error) {
      if (owned.current()) setFeedback({ busy: false, error: errorMessage(error) });
    } finally {
      if (owned.current()) { feedbackBusy.current = false; owned.finish(); }
    }
  }

  function historyKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || (event.target as HTMLElement).closest("button,input,select,textarea,a,[contenteditable=true]")) return;
    const next = event.key === "ArrowLeft" ? cursor - 1 : event.key === "ArrowRight" ? cursor + 1 : event.key === "Home" ? 0 : event.key === "End" ? positions.length - 1 : null;
    if (next === null) return;
    event.preventDefault(); event.stopPropagation(); setPly(Math.max(0, Math.min(next, positions.length - 1)));
  }

  return <section className="game-review" aria-label="Game Review">
    <header className="game-review-header"><div><h1>Game review</h1><p className="game-review-muted">Review original games and repertoire findings.</p></div>
      <Button onClick={() => setReload(value => value + 1)} pending={recent.loading}>Refresh games and status</Button>
    </header>
    <div className="game-review-sync" aria-label="Provider synchronization">
      {(["lichess", "chesscom"] as const).map(provider => {
        const current = refresh.progress[provider];
        const status = current.job;
        const label = status?.status === "running" ? "Fetching" : status?.status === "retryable" ? "Retrying"
          : status?.status === "complete" ? "Completed" : status?.status === "failed" || status?.status === "quarantined" ? "Failed"
          : current.pending ? "Queued" : "Ready";
        return <div key={provider}><p role="status">{providerLabel(provider)} refresh: {label}</p>
          {status && <p className="game-review-muted">{status.window.start_at} – {status.window.end_at} · {status.pages_committed} pages</p>}
          {status?.status === "retryable" && status.next_attempt_at > 0 && <p>Next attempt: {new Date(status.next_attempt_at * 1000).toISOString()}</p>}
          {(current.error || status?.error) && <p role="alert" className="game-review-error">{current.error ?? status?.error}</p>}
          <Button disabled={!recent.data} pending={current.pending} onClick={() => refresh.refresh(provider)}>
            Refresh {providerLabel(provider)} now
          </Button></div>;
      })}
    </div>
    {recent.loading && <p role="status">Loading recent games…</p>}
    {recent.error && <div role="alert" className="game-review-error">{recent.error} <Button onClick={() => setReload(value => value + 1)}>Retry recent games</Button></div>}
    {recent.data && recent.data.games.length === 0 && <p>No imported games yet. Use a provider sync when ready.</p>}
    <div className="game-review-layout"><aside aria-label="Recent games"><h2>Recent games</h2><ul className="game-review-games">
      {recent.data?.games.map(game => <li key={gameKey(game)}><Button className="game-review-game" aria-pressed={selected !== null && gameKey(selected) === gameKey(game)} onClick={() => setSelected(game)}>
        <strong>{providerLabel(game.provider)} · {game.game_id}</strong><span>{gameTime(game)}</span><span>Played as {game.player_color}</span><span>Sync: {recent.data?.sync.find(status => status.provider === game.provider)?.status ?? "unknown"}</span>
      </Button></li>)}
    </ul></aside><article className="game-review-detail" aria-label="Selected game">
      {!selected && <p>Select a game to review its original move history.</p>}
      {selected && (detail.loading || detail.identity !== selectionIdentity) && <p role="status">Loading game…</p>}
      {detail.identity === selectionIdentity && detail.error && <div role="alert" className="game-review-error">{detail.error} <Button onClick={() => setDetailReload(value => value + 1)}>Retry game</Button></div>}
      {currentDetail && <><h2>{providerLabel(currentDetail.provider)} · {currentDetail.game_id}</h2><p className="game-review-muted">{gameTime(currentDetail)}</p>
        <section className="game-review-summary" aria-label="First repertoire departure" data-testid="first-departure-summary" data-status={selection?.status ?? "unknown"} data-progress-stage={departureProgressStage(primaryReceipt)}>
          <h3>{selection ? departureLabels[selection.status] : "Comparison result not yet available"}</h3>
          {selection?.ply && <p>{selection.actor === "learner" ? "Your move" : "Opponent move"}: {departureMove(positions, selection.ply, selection.played_uci!)} · Ply {selection.ply}</p>}
          {selection?.ply && <dl><dt>Played</dt><dd>{departureMove(positions, selection.ply, selection.played_uci!)}</dd>
            <dt>Expected</dt><dd>{selection.expected_uci.map(move => departureMove(positions, selection.ply!, move)).join(", ") || "No authored response"}</dd></dl>}
          {selection?.status === "own_actionable" && <><p>Study target: {primaryProgress ? primaryProgress.target.card_id : selection.decision!.presentation_id}</p>
            {primaryProgress && primaryProgress.target.card_id !== selection.decision!.presentation_id && <p className="game-review-muted">Original presentation: {selection.decision!.presentation_id}. The Study owner retained the original effect on the current presentation.</p>}
            <p role="status">{departureProgress(primaryReceipt)}</p></>}
          {selection?.status === "own_unmapped" && <p>No exact Study target is available. Automatic feedback is held.</p>}
          {selection && ["opponent_gap", "coverage_end", "missing_own_continuation", "unresolved"].includes(selection.status) && <p>No automatic Study consequence is authorized by this result.</p>}
          {selection?.diagnostic && <p>Comparison detail: {selection.diagnostic.replaceAll("_", " ")}</p>}
          {currentDetail.comparison_freshness !== "current" && <p className="game-review-muted">{currentDetail.comparison_freshness === "stale" ? "Recorded comparison is stale; source or repertoire facts have changed." : "Comparison freshness is unknown; current repertoire scope has not been verified."}</p>}
        </section>
        <div role="region" aria-label="Original game history" tabIndex={0} onKeyDown={historyKeys}>
          <div className="game-review-board"><Board {...boardPosition(selectionIdentity, positions, currentDetail.moves, cursor, currentDetail.player_color)} /></div>
          <div className="game-review-controls"><Button disabled={cursor === 0} onClick={() => setPly(0)}>First</Button><Button disabled={cursor === 0} onClick={() => setPly(cursor - 1)}>Previous</Button>
            <span role="status">Ply {cursor} of {currentDetail.moves.length}</span><Button disabled={cursor === currentDetail.moves.length} onClick={() => setPly(cursor + 1)}>Next</Button><Button disabled={cursor === currentDetail.moves.length} onClick={() => setPly(currentDetail.moves.length)}>Last</Button></div>
        </div>
        <h3>Per-repertoire diagnostic details</h3>{!currentDetail.findings.length && <p>No repertoire findings for this game.</p>}
        {feedback.error && <p role="alert" className="game-review-error">{feedback.error}</p>}
        <ul className="game-review-findings">{currentDetail.findings.map(finding => {
          const receipt = receiptFor(currentDetail.receipts, finding);
          const gap = finding.kind === "opponent_gap" || finding.kind === "missing_own_continuation";
          // The existing retry endpoint replays stored lapse deliveries only.
          const retryable = matchesSelectedDeparture(finding) && (!receipt || !("admission_status" in receipt)
            && receipt.status === "pending" && ["accepted", "duplicate"].includes(receipt.outcome));
          return <li key={findingKey(finding)} className="game-review-finding" data-current={cursor === finding.ply - 1 || cursor === finding.ply}>
            <strong>{findingLabels[finding.kind]}</strong><p>Ply {finding.ply}{finding.diagnostic ? ` · ${finding.diagnostic}` : ""}</p>
            <dl><dt>Played</dt><dd>{finding.played_uci}</dd><dt>Expected</dt><dd>{finding.expected_uci.join(", ") || "Not known"}</dd><dt>Repertoire</dt><dd>{finding.repertoire_id}</dd><dt>Decision</dt><dd>{finding.decision_id ?? "Not mapped"}</dd><dt>Card / presentation</dt><dd>{finding.presentation_id ?? "Not mapped"}</dd></dl>
            <p role="status">{receiptLabel(receipt)}</p>{receipt?.available_by && <p>Availability deadline: {receipt.available_by}</p>}
            <div className="game-review-header"><Button onClick={() => setPly(finding.ply - 1)}>Show position before ply {finding.ply}</Button>
              {gap && onReviewGap && <Button onClick={() => onReviewGap({ kind: "review_repertoire_gap", provider: finding.provider, game_id: finding.game_id, source_revision: finding.source_revision, repertoire_id: finding.repertoire_id, repertoire_revision: finding.repertoire_revision, position_key: finding.position_key, ply: finding.ply })}>Review repertoire gap at ply {finding.ply}</Button>}
              {finding.kind === "confirmed_miss" && retryable && <Button pending={feedback.busy} onClick={() => void retryFeedback(finding)}>Retry feedback at ply {finding.ply}</Button>}
            </div>
          </li>;
        })}</ul>
      </>}
    </article></div>
  </section>;
}
