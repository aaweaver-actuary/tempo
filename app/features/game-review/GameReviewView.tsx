"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Board } from "@tempo/board-ui";
// The package's exported stylesheet; root package registration belongs to #491.
import "../../../packages/board-ui/styles/board.css";
import { Button } from "../../components/buttons/BaseButton";
import type { Finding, GameDetail, GameSummary, ProviderSyncStatus, RecentGames } from "../product-api/contracts";
import type { GameReviewClient } from "./api";
import { boardPosition, findingLabels, gamePositions, OperationKeys, OwnedRequests, receiptLabel } from "./state";
import "./game-review.css";

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
  const syncBusy = useRef(new Set<string>());
  const feedbackBusy = useRef(false);
  const [recent, setRecent] = useState<Load<RecentGames>>({ ...emptyLoad<RecentGames>(), loading: true });
  const [selected, setSelected] = useState<GameSummary | null>(null);
  const [detail, setDetail] = useState<Load<GameDetail> & { identity?: string }>(emptyLoad);
  const [sync, setSync] = useState<readonly ProviderSyncStatus[]>([]);
  const [syncSubmitting, setSyncSubmitting] = useState(0);
  const [reload, setReload] = useState(0);
  const [detailReload, setDetailReload] = useState(0);
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
      setSync(data.sync);
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
          || finding.source_revision !== data.source_revision || finding.ply > data.moves.length || data.moves[finding.ply - 1] !== finding.played_uci
          || repertoireId !== undefined && finding.repertoire_id !== repertoireId)) throw new Error("Findings do not match this game history or repertoire. Refresh before reviewing.");
        setDetail({ data, loading: false, error: null, identity: selectionIdentity });
        setPly(data.findings[0] ? Math.max(0, data.findings[0].ply - 1) : 0);
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

  async function syncProvider(provider: GameSummary["provider"]) {
    const scope = `sync:${provider}`;
    const previous = sync.find(status => status.provider === provider);
    if (syncBusy.current.has(scope) || previous?.status === "pending") return;
    syncBusy.current.add(scope);
    setSyncSubmitting(value => value + 1);
    requests.current.cancel("recent");
    setRecent(value => ({ ...value, loading: false }));
    const operation_id = operations.current.get(scope, previous?.status === "failed" ? previous.operation_id : null);
    const owned = requests.current.start(scope);
    const update = (status: ProviderSyncStatus) => setSync(values => [...values.filter(item => item.provider !== provider), status]);
    update({ provider, status: "pending", operation_id, error: null });
    try {
      const status = await api.sync({ provider, operation_id, signal: owned.signal });
      if (!owned.current()) return;
      if (status.provider !== provider || status.operation_id !== operation_id) throw new Error("Sync receipt does not match the original operation.");
      update(status);
      if (status.status === "complete") { operations.current.complete(scope); setReload(value => value + 1); }
    } catch (error) {
      if (owned.current()) update({ provider, status: "failed", operation_id, error: errorMessage(error) });
    } finally {
      if (owned.current()) { syncBusy.current.delete(scope); setSyncSubmitting(value => value - 1); owned.finish(); }
    }
  }

  async function retryFeedback(finding: Finding) {
    if (feedbackBusy.current || finding.kind !== "confirmed_miss") return;
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
      <Button onClick={() => setReload(value => value + 1)} pending={recent.loading || syncSubmitting > 0}>Refresh games and status</Button>
    </header>
    <div className="game-review-sync" aria-label="Provider synchronization">
      {(["lichess", "chesscom"] as const).map(provider => {
        const status = sync.find(item => item.provider === provider);
        return <div key={provider}><p role="status">{providerLabel(provider)} sync: {status?.status ?? "unknown"}</p>
          {status?.error && <p role="alert" className="game-review-error">{status.error}</p>}
          <Button disabled={!recent.data} pending={status?.status === "pending"} onClick={() => void syncProvider(provider)}>
            {status?.status === "failed" ? "Retry" : "Sync"} {providerLabel(provider)}
          </Button></div>;
      })}
    </div>
    {recent.loading && <p role="status">Loading recent games…</p>}
    {recent.error && <div role="alert" className="game-review-error">{recent.error} <Button onClick={() => setReload(value => value + 1)}>Retry recent games</Button></div>}
    {recent.data && recent.data.games.length === 0 && <p>No imported games yet. Use a provider sync when ready.</p>}
    <div className="game-review-layout"><aside aria-label="Recent games"><h2>Recent games</h2><ul className="game-review-games">
      {recent.data?.games.map(game => <li key={gameKey(game)}><Button className="game-review-game" aria-pressed={selected !== null && gameKey(selected) === gameKey(game)} onClick={() => setSelected(game)}>
        <strong>{providerLabel(game.provider)} · {game.game_id}</strong><span>{gameTime(game)}</span><span>Played as {game.player_color}</span><span>Sync: {sync.find(status => status.provider === game.provider)?.status ?? "unknown"}</span>
      </Button></li>)}
    </ul></aside><article className="game-review-detail" aria-label="Selected game">
      {!selected && <p>Select a game to review its original move history.</p>}
      {selected && (detail.loading || detail.identity !== selectionIdentity) && <p role="status">Loading game…</p>}
      {detail.identity === selectionIdentity && detail.error && <div role="alert" className="game-review-error">{detail.error} <Button onClick={() => setDetailReload(value => value + 1)}>Retry game</Button></div>}
      {currentDetail && <><h2>{providerLabel(currentDetail.provider)} · {currentDetail.game_id}</h2><p className="game-review-muted">{gameTime(currentDetail)}</p>
        <div role="region" aria-label="Original game history" tabIndex={0} onKeyDown={historyKeys}>
          <div className="game-review-board"><Board {...boardPosition(selectionIdentity, positions, currentDetail.moves, cursor, currentDetail.player_color)} /></div>
          <div className="game-review-controls"><Button disabled={cursor === 0} onClick={() => setPly(0)}>First</Button><Button disabled={cursor === 0} onClick={() => setPly(cursor - 1)}>Previous</Button>
            <span role="status">Ply {cursor} of {currentDetail.moves.length}</span><Button disabled={cursor === currentDetail.moves.length} onClick={() => setPly(cursor + 1)}>Next</Button><Button disabled={cursor === currentDetail.moves.length} onClick={() => setPly(currentDetail.moves.length)}>Last</Button></div>
        </div>
        <h3>Repertoire findings</h3>{!currentDetail.findings.length && <p>No repertoire findings for this game.</p>}
        {feedback.error && <p role="alert" className="game-review-error">{feedback.error}</p>}
        <ul className="game-review-findings">{currentDetail.findings.map(finding => {
          const receipt = receiptFor(currentDetail.receipts, finding);
          const gap = finding.kind === "opponent_gap" || finding.kind === "missing_own_continuation";
          const retryable = !receipt || receipt.status === "pending" && ["accepted", "duplicate"].includes(receipt.outcome);
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
