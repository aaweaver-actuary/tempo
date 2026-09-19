"use client";
import { Notice, useTaskTabs } from "../components/task-tabs";
import { BoardTools } from "../components/board-workspace";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DrawShape } from "@lichess-org/chessground/draw";
import {
  Chessboard,
  type BoardTheme,
  type PieceSet,
} from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { API_URL, STANDARD_FEN } from "../const";
import {
  readWorkspaceResponse,
  invalidateWorkspaceData,
} from "../lib/workspace-data";
import { requestInteractiveAnalysis } from "../lib/engine-broker";
import type { GameSyncState } from "../hooks/use-game-sync";
import { importGameAndReformatToGameViewRecord } from "../utils/pgn";
import { importGameSummaryToGameViewRecord } from "../utils/pgn";
import { fenAfterMoves } from "../utils/fen";
import { convertSanToUci } from "../utils/chess";
import { usesLocalApi } from "../utils/local";
import { lichessAnalysisUrl } from "../utils/urls";
import {
  asFenString,
  asLineId,
  asRepertoireId,
  asSanMove,
  type GameId,
  type GameViewRecord,
  type AnalysisLine,
} from "../types";
import { canonicalFenKey } from "../utils/canonical-line";
import { useBackgroundStudy } from "../hooks/use-background-study";
import type { StudyTask } from "../lib/study-computation";
import type { IndexedPosition } from "../lib/position-similarity";
import { sampleGames } from "../samples";
import { Chess, type Square } from "chess.js";
import { gameRecordSchema } from "../domain/schemas";

type GuidedReviewSession = {
  id: string;
  game_id: string;
  status: "active" | "complete";
  current_index: number;
  total: number;
  current: null | {
    finding_id: string;
    kind: string;
    ply: number;
    fen: string;
    motif?: string | null;
    confidence: number;
  };
  attempts: Array<{ finding_id: string; move_uci: string; correct: 0 | 1; attempted_at: string }>;
};
type GuidedReviewAttempt = {
  correct: boolean;
  revealed: GuidedReviewSession["current"] & {
    answer: {
      actual_move_uci?: string | null;
      best_move_uci?: string | null;
      expected_moves: string[];
      loss_cp?: number | null;
      eval_before_cp?: number | null;
      eval_after_cp?: number | null;
      principal_variation: string[];
    };
  };
  session: GuidedReviewSession;
};

export default function GamesView({
  onAnalyze,
  onSettings,
  onSync,
  onRepair,
  syncState,
  theme,
  pieceSet,
  useSharedBoard = false,
  initialFenFilter = "",
  onClearFenFilter,
}: {
  onAnalyze: (game: GameViewRecord, cursor: number) => void;
  onSettings: () => void;
  onSync: () => void;
  onRepair?: () => void;
  syncState: GameSyncState;
  theme: BoardTheme;
  pieceSet: PieceSet;
  useSharedBoard?: boolean;
  initialFenFilter?: string;
  onClearFenFilter?: () => void;
}) {
  const local = usesLocalApi();
  const tools = useTaskTabs(["Moves", "Analysis", "Library"], "Moves", "tempo-games-tools");
  const [loaded, setLoaded] = useState(!local);
  const [pageCursor, setPageCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [nextPageCursor, setNextPageCursor] = useState<string | null>(null);
  const [records, setRecords] = useState<GameViewRecord[]>(() =>
    local ? [] : sampleGames,
  );
  const [savedSession] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem('tempo-games-session') ?? 'null');
      return saved?.version === 1 && typeof saved.id === 'string' && Number.isInteger(saved.cursor) && saved.cursor >= 0 ? saved as {id:GameId;cursor:number} : null;
    } catch { return null; }
  });
  const [selectedId, setSelectedId] = useState<GameId | "">(savedSession?.id ?? "");
  const [cursor, setCursor] = useState(savedSession?.cursor ?? 0);
  useEffect(() => { sessionStorage.setItem('tempo-games-session', JSON.stringify({version:1,id:selectedId,cursor})); }, [selectedId,cursor]);
  const selectedIdRef = useRef(selectedId);
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);
  const [engineOn, setEngineOn] = useState(
    () => localStorage.getItem("tempo-games-engine-on") !== "false",
  );
  const [engineText, setEngineText] = useState("");
  const [error, setError] = useState("");
  const [findings, setFindings] = useState<Array<{ id: string; game_id: string; ply: number; kind: string; confidence: number; motif?: string | null; card_id?: string | null }>>([]);
  const [cardPreviews, setCardPreviews] = useState<Record<string, { starting_fen: string; moves: string[]; best_move: string; existing_card_id?: string | null }>>({});
  const [positionSummary, setPositionSummary] = useState<{
    encounters: number;
    analyzed_encounters: number;
    moves: Array<{ move_uci: string; games: number; score_percentage: number; average_loss_cp: number | null; mistakes: number }>;
  } | null>(null);
  const [guidedReview, setGuidedReview] = useState<GuidedReviewSession | null>(null);
  const [guidedReveal, setGuidedReveal] = useState<GuidedReviewAttempt | null>(null);
  const [lines, setLines] = useState<AnalysisLine[]>([]);
  const { setShellBoardForOwner, releaseShellBoardForOwner } = useBoardPublisher();
  const [filters, setFilters] = useState(() => ({
    source: "All",
    status: "All",
    color: "All",
    speed: "All",
    result: "All",
    from: new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10),
  }));
  const selected = records.find((game) => game.id === selectedId) ?? records[0];
  const gameFen = selected
    ? fenAfterMoves(
        selected.moves,
        Math.min(cursor, selected.moves.length),
        selected.startFen,
      )
    : STANDARD_FEN;
  const gameLast =
    selected && cursor
      ? convertSanToUci(selected.moves, selected.startFen)[cursor - 1]
      : undefined;
  const indexTask = useMemo<StudyTask>(
    () => ({ kind: "index", lines }),
    [lines],
  );
  const positions = useBackgroundStudy<IndexedPosition[]>(indexTask, []);
  const shapes = positions
    .filter(
      (position) =>
        canonicalFenKey(position.fen) === canonicalFenKey(gameFen) &&
        position.nextUci &&
        (!selected?.repertoireId ||
          position.repertoireId === selected.repertoireId),
    )
    .map(
      (position) =>
        ({
          orig: position.nextUci!.slice(0, 2),
          dest: position.nextUci!.slice(2, 4),
          brush: "yellow",
        }) as DrawShape,
    );
  const games = records.filter(
    (game) =>
      game.date >= filters.from &&
      (["source", "status", "color", "speed", "result"] as const).every(
        (field) => filters[field] === "All" || filters[field] === game[field],
      ),
  );
  const summaryUrl = useMemo(() => {
    const parameters = new URLSearchParams();
    if (initialFenFilter) parameters.set("fen", initialFenFilter);
    if (pageCursor) parameters.set("cursor", pageCursor);
    if (filters.source !== "All")
      parameters.set("provider", filters.source === "Chess.com" ? "chess.com" : "lichess");
    if (filters.status !== "All") parameters.set("status", filters.status);
    if (filters.color !== "All") parameters.set("color", filters.color);
    if (filters.speed !== "All") parameters.set("speed", filters.speed);
    if (filters.result !== "All") parameters.set("outcome", filters.result);
    if (filters.from) parameters.set("played_from", filters.from);
    const query = parameters.toString();
    return `${API_URL}/api/games/summary${query ? `?${query}` : ""}`;
  }, [filters, initialFenFilter, pageCursor]);
  const loadGames = useCallback(async () => {
    if (!local) return;
    try {
      const response = await readWorkspaceResponse(summaryUrl);
      if (!response.ok) throw new Error("Could not load your local games.");
      const body = (await response.json()) as {
        games: Record<string, unknown>[];
        next_cursor?: string | null;
      };
      const loaded = body.games.map(importGameSummaryToGameViewRecord);
      setNextPageCursor(body.next_cursor ?? null);
      setRecords(loaded);
      if (!loaded.some((game) => game.id === selectedIdRef.current)) {
        setSelectedId(loaded[0]?.id ?? "");
        setCursor(loaded[0]?.flagPly ?? 0);
      }
      setError("");
      setLoaded(true);
      if (initialFenFilter) {
        const positionResponse = await readWorkspaceResponse(
          `${API_URL}/api/games/position-summary?fen=${encodeURIComponent(initialFenFilter)}`,
        );
        if (positionResponse.ok)
          setPositionSummary(await positionResponse.json());
      } else {
        setPositionSummary(null);
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load games.",
      );
    }
  }, [local, initialFenFilter, summaryUrl]);
  useEffect(() => {
    if (!local || !selectedId) return;
    const selectedSummary = records.find((game) => game.id === selectedId);
    if (!selectedSummary || selectedSummary.moves.length > 0) return;
    const requestedId = selectedId;
    const controller = new AbortController();
    void fetch(`${API_URL}/api/games/${encodeURIComponent(requestedId)}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Could not load the selected game.");
        const detailedGame = importGameAndReformatToGameViewRecord(
          gameRecordSchema.parse(await response.json()),
        );
        if (!detailedGame || selectedIdRef.current !== requestedId) return;
        setRecords((current) => current.map((game) => game.id === requestedId ? detailedGame : game));
        setCursor((current) => Math.min(current || detailedGame.flagPly, detailedGame.moves.length));
      })
      .catch((reason) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Could not load the selected game.");
      });
    return () => controller.abort();
  }, [local, records, selectedId]);
  useEffect(() => {
    if (syncState.lastSuccess) invalidateWorkspaceData();
    queueMicrotask(() => void loadGames());
  }, [loadGames, syncState.lastSuccess]);
  useEffect(() => {
    const applySuccessfulRefresh = (event: Event) => {
      const detail = (event as CustomEvent<{ state: string; url: string }>).detail;
      if (detail?.state === "ready" && detail.url === summaryUrl)
        void loadGames();
    };
    window.addEventListener("tempo-workspace-data", applySuccessfulRefresh);
    return () =>
      window.removeEventListener("tempo-workspace-data", applySuccessfulRefresh);
  }, [loadGames, summaryUrl]);
  useEffect(() => {
    if (!local) return;
    void readWorkspaceResponse(`${API_URL}/api/repertoire/lines`)
      .then(async (response) => {
        if (!response.ok) return;
        const body = (await response.json()) as {
          lines: Record<string, unknown>[];
        };
        setLines(
          body.lines.map((line: Record<string, unknown>) => ({
            id: asLineId(String(line.id)),
            repertoireId: asRepertoireId(String(line.repertoire_id)),
            repertoireName: String(line.repertoire_name),
            title: String(line.name),
            side: line.trained_color === "black" ? "black" : "white",
            startingFen: asFenString(String(line.start_fen)),
            moves: (Array.isArray(line.moves) ? line.moves : []).map((move) =>
              asSanMove(String(move)),
            ),
          })),
        );
      })
      .catch(() => undefined);
  }, [local]);
  const loadFindings = useCallback(async () => {
    if (!local || !selectedIdRef.current) {
      setFindings([]);
      return;
    }
    const findingResponse = await fetch(
      `${API_URL}/api/game-findings?status=pending&game_id=${encodeURIComponent(selectedIdRef.current)}`,
    );
    if (findingResponse.ok) {
      const payload = (await findingResponse.json()) as { findings?: typeof findings };
      setFindings(payload.findings ?? []);
    }
  }, [local]);
  useEffect(() => {
    queueMicrotask(() => void loadFindings());
  }, [loadFindings, syncState.lastSuccess, selectedId]);
  async function decideFinding(findingId: string, decision: "accepted" | "ignored") {
    const response = await fetch(`${API_URL}/api/game-findings/${findingId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    if (!response.ok) setError("Could not save that gameplay decision.");
    else await loadFindings();
  }
  async function excludeSelectedGame() {
    if (!selected) return;
    const response = await fetch(`${API_URL}/api/games/${encodeURIComponent(selected.id)}/exclusion`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded: true }),
    });
    if (!response.ok) setError("Could not exclude this game from adaptation.");
    else await loadFindings();
  }
  async function createFindingCard(findingId: string, save: boolean) {
    const response = await fetch(`${API_URL}/api/game-findings/${findingId}/card`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ save }),
    });
    if (!response.ok) {
      setError("Could not prepare that game position as a study card.");
      return;
    }
    const payload = (await response.json()) as { preview: { starting_fen: string; moves: string[]; best_move: string; existing_card_id?: string | null }; saved: boolean };
    setCardPreviews((current) => ({ ...current, [findingId]: payload.preview }));
    if (payload.saved) await loadFindings();
  }
  async function startGuidedReview() {
    if (!selected) return;
    const response = await fetch(
      `${API_URL}/api/games/${encodeURIComponent(selected.id)}/guided-review`,
      { method: "POST" },
    );
    if (!response.ok) {
      setError("Could not start this guided review.");
      return;
    }
    setGuidedReview((await response.json()) as GuidedReviewSession);
    setGuidedReveal(null);
  }
  const attemptGuidedMove = useCallback(async (from: Square, to: Square) => {
    if (!guidedReview?.current || guidedReveal) return;
    const board = new Chess(guidedReview.current.fen);
    const legalMove = board.move({ from, to, promotion: "q" });
    if (!legalMove) return;
    const moveUci = `${legalMove.from}${legalMove.to}${legalMove.promotion ?? ""}`;
    const response = await fetch(`${API_URL}/api/guided-reviews/${guidedReview.id}/attempt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ move_uci: moveUci }),
    });
    if (!response.ok) {
      setError("Could not save that correction attempt.");
      return;
    }
    setGuidedReveal((await response.json()) as GuidedReviewAttempt);
  }, [guidedReview, guidedReveal]);
  const displayedFen = guidedReveal?.revealed.fen ?? guidedReview?.current?.fen ?? gameFen;
  useEffect(() => {
    let active = true;
    if (!engineOn || !selected) return;
    const debounceTimer = window.setTimeout(() => {
      if (active) setEngineText("Analyzing…");
      void requestInteractiveAnalysis(displayedFen, 12)
      .then((moves) => {
        if (active)
          setEngineText(
            moves[0]
              ? `${moves[0].san} · ${moves[0].score}`
              : "Terminal position",
          );
      })
      .catch((reason) => {
        if (active) setEngineText(`Engine error: ${reason.message}`);
      });
    }, 200);
    return () => {
      active = false;
      window.clearTimeout(debounceTimer);
    };
  }, [engineOn, displayedFen, selected]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.target as HTMLElement)?.matches(
          'input,textarea,select,[contenteditable="true"]',
        ) ||
        document.querySelector('[role="dialog"]')
      )
        return;
      const length = selected?.moves.length ?? 0;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setCursor((value) => Math.max(0, value - 1));
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setCursor((value) => Math.min(length, value + 1));
      }
      if (["Home", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        setCursor(0);
      }
      if (["End", "ArrowDown"].includes(event.key)) {
        event.preventDefault();
        setCursor(length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected?.moves.length]);
  const matchedDecisions = games.reduce((total, game) => total + (game.matchedPlayerDecisions ?? 0), 0);
  const repertoireOpportunities = games.reduce((total, game) => total + (game.repertoireOpportunities ?? 0), 0);
  const selectedFindings = findings.filter((finding) => finding.game_id === selected?.id);

  useEffect(() => {
    if (!useSharedBoard) return;
    setShellBoardForOwner("games", {
      unavailable: !selected ? (error ? "Game position unavailable. Retry the local service." : loaded ? "Select an imported game to review." : "Loading games…") : undefined,
      fen: displayedFen,
      lastMove: !guidedReview && gameLast
        ? ([gameLast.slice(0, 2), gameLast.slice(2, 4)] as readonly [
            string,
            string,
          ])
        : undefined,
      shapes,
      interactionMode: guidedReview?.current && !guidedReveal ? "legal" : "readonly",
      showHint: false,
      theme,
      pieceSet,
      orientation: selected?.color === "black" ? "black" : "white",
      positionRevision: cursor,
      onMove: guidedReview?.current && !guidedReveal ? attemptGuidedMove : undefined,
      onSquareSelect: undefined,
      onFreeMove: undefined,
      onDrawnShapesChange: undefined,
      onFlip: undefined,
    });
    return () => releaseShellBoardForOwner("games");
  }, [
    loaded,
    error,
    selected,
    cursor,
    displayedFen,
    gameLast,
    pieceSet,
    releaseShellBoardForOwner,
    selected?.color,
    setShellBoardForOwner,
    shapes,
    theme,
    useSharedBoard,
    guidedReview,
    guidedReveal,
    attemptGuidedMove,
  ]);

  return (
    <section className="games-page" {...tools.panelProps}>
      <div className="page-heading compact">
        <div>
          <h1 className="sr-only">Games{!local ? " · Demo" : ""}</h1>
          <p>
            {syncState.lastSuccess
              ? `Last sync ${new Date(syncState.lastSuccess).toLocaleString()}`
              : "No successful sync yet"}
          </p>
        </div>
        {local && (
          <div>
            <button className="primary-button sync-button" onClick={onSync} disabled={syncState.syncing} aria-label={syncState.syncing ? "Syncing games" : "↻ Sync games"}>
              {syncState.syncing && <i />}
              {syncState.syncing ? "Syncing games" : "↻ Sync now"}
            </button>
            <button onClick={() => onRepair?.()} disabled={syncState.syncing}>Repair last 90 days</button>
          </div>
        )}
      </div>
      {local && <p className="muted">Import filter: {syncState.filterLabel ?? "Rated blitz, rapid, and classical · last 90 days"}. Bullet, casual, variants, and older games are skipped.</p>}
      {initialFenFilter && (
        <section className="position-game-summary" aria-label="Games from reviewed position">
          <p>
            Position filter · {positionSummary?.encounters ?? 0} encounters · {positionSummary?.analyzed_encounters ?? 0} analyzed
            {" "}<button onClick={onClearFenFilter}>Clear</button>
          </p>
          {positionSummary?.moves.map((move) => (
            <p key={move.move_uci}>
              <strong>{move.move_uci}</strong> · {move.games} games · {move.score_percentage}% score
              {move.average_loss_cp === null ? " · awaiting analysis" : ` · ${move.average_loss_cp} cp average loss · ${move.mistakes} mistakes`}
            </p>
          ))}
          {positionSummary?.encounters === 0 && <p>No imported game has reached this position.</p>}
        </section>
      )}
      {(syncState.providers ?? []).map((provider) => (
        <p className="muted" key={provider.provider}>
          {provider.provider}: {provider.inserted} new, {provider.updated} updated, {provider.duplicates} duplicates, {provider.filtered} filtered, {provider.rejected} rejected{provider.failed ? ", failed" : ""}
        </p>
      ))}
      {(error || syncState.error) && (
        <p role="alert">
          {error || syncState.error}{" "}
          {error && <button onClick={() => { invalidateWorkspaceData(); void loadGames(); }}>Retry</button>}
          <button onClick={onSettings}>Account settings</button>
        </p>
      )}
      {!local && (
        <p>
          Sample comparisons. Import and sync your games in{" "}
          <a href="https://github.com/aaweaver-actuary/tempo#running-locally">
            full local Tempo
          </a>
          .
        </p>
      )}
      {tools.tabs}
      {!loaded && !error && <Notice>Loading games…</Notice>}
      {(loaded || records.length > 0) && <div className="game-review">
        <div className="game-board">
          {!useSharedBoard && (
            <Chessboard
              fen={displayedFen}
              lastMove={
                gameLast
                  ? [gameLast.slice(0, 2), gameLast.slice(2, 4)]
                  : undefined
              }
              shapes={shapes}
              locked={!guidedReview?.current || Boolean(guidedReveal)}
              showHint={false}
              theme={theme}
              pieceSet={pieceSet}
              onMove={(from, to) => void attemptGuidedMove(from, to)}
              orientation={selected?.color}
            />
          )}
          <BoardTools>
            <button
              disabled={!selected || cursor === 0}
              onClick={() => setCursor((value) => Math.max(0, value - 1))}
            >
              ← Back
            </button>
            <button disabled={!selected || cursor >= selected.moves.length}
              onClick={() =>
                setCursor((value) =>
                  Math.min(selected?.moves.length ?? 0, value + 1),
                )
              }
            >
              Forward →
            </button>
            <button
              disabled={!selected}
              onClick={() => setCursor(selected?.flagPly ?? 0)}
            >
              ⚑ First mistake
            </button>
            <button
              className={engineOn ? "active" : ""}
              onClick={() =>
                setEngineOn((value) => {
                  localStorage.setItem("tempo-games-engine-on", String(!value));
                  return !value;
                })
              }
            >
              Stockfish
            </button>
          </BoardTools>
        </div>
        <div className="game-side-scroll">
          <aside className="game-inspector" data-task="Moves">
            <h2>{selected?.opening ?? "No games imported"}</h2>
            <strong>{selected?.flag}</strong>
            {engineOn && <p>{engineText}</p>}
            <div className="game-moves">
              {selected?.moves.map((move, index) => (
                <button
                  className={`${index < cursor ? "shown" : ""}${index === selected.flagPly ? " flagged" : ""}`}
                  onClick={() => setCursor(index + 1)}
                  key={index}
                  title={[...(selected.timeline ?? []), ...selectedFindings].filter((event) => event.ply === index).map((event) => event.kind).join(", ")}
                >
                  {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}
                  {move}
                  {[...(selected.timeline ?? []), ...selectedFindings].some((event) => event.ply === index) ? " •" : ""}
                </button>
              ))}
            </div>
            {selected && (
              <>
                <button className="primary-button" onClick={() => void startGuidedReview()}>
                  Review this game
                </button>
                <button
                  className="primary-button"
                  onClick={() => onAnalyze(selected, cursor)}
                >
                  Open position in Builder
                </button>
                <a
                  href={lichessAnalysisUrl(
                    selected.moves.slice(0, cursor),
                    selected.startFen,
                  )}
                  target="_blank"
                  rel="noreferrer"
                >
                  Lichess analysis ↗
                </a>
              </>
            )}
          </aside>
          <div className="games-metrics" data-task="Analysis">
            {guidedReview && (
              <article className="guided-game-review" aria-live="polite">
                <span>Guided review</span>
                <strong>{guidedReview.status === "complete" ? "Complete" : `${guidedReview.current_index + 1} of ${guidedReview.total}`}</strong>
                {guidedReview.current && !guidedReveal && (
                  <p>Find a correction for this {guidedReview.current.kind}. Play it on the board.</p>
                )}
                {guidedReveal && (
                  <>
                    <p>{guidedReveal.correct ? "That correction works." : "There is a stronger correction."}</p>
                    <small>
                      Actual {guidedReveal.revealed.answer.actual_move_uci ?? "—"} · Recommended {guidedReveal.revealed.answer.best_move_uci ?? guidedReveal.revealed.answer.expected_moves[0] ?? "—"}
                      {guidedReveal.revealed.answer.loss_cp != null ? ` · ${guidedReveal.revealed.answer.loss_cp} cp` : ""}
                    </small>
                    <p>{guidedReveal.revealed.answer.principal_variation.join(" ")}</p>
                    <button onClick={() => {
                      setGuidedReview(guidedReveal.session);
                      setGuidedReveal(null);
                    }}>{guidedReveal.session.status === "complete" ? "Finish review" : "Next correction"}</button>
                    {selected && <button onClick={() => onAnalyze(selected, guidedReveal.revealed.ply)}>Open full Builder analysis</button>}
                  </>
                )}
                {guidedReview.status === "complete" && !guidedReveal && <p>All selected corrections reviewed. No study scheduling changed.</p>}
              </article>
            )}
            <article>
              <span>Repertoire adherence</span>
              <strong>
                {repertoireOpportunities
                  ? `${Math.round((matchedDecisions / repertoireOpportunities) * 100)}%`
                  : "—"}
              </strong>
              <small>
                {matchedDecisions} of {repertoireOpportunities} player decisions
              </small>
            </article>
            {["opponent gap", "player deviation"].map((value) => (
              <article key={value}>
                <span>{value}</span>
                <strong>
                  {games.filter((game) => game.status === value).length}
                </strong>
              </article>
            ))}
            {selected && <article>
              <span>Adaptation review</span>
              <strong>{selectedFindings.length}</strong>
              <button onClick={excludeSelectedGame}>Ignore this game for adaptation</button>
            </article>}
            {selectedFindings.map((finding) => (
              <article key={finding.id}>
                <span>{finding.kind}{finding.kind === "tactical miss" ? ` · ${finding.confidence >= 0.8 ? finding.motif : "unclassified"}` : ""}</span>
                <small>Move {Math.floor(finding.ply / 2) + 1}</small>
                {finding.kind === "repertoire lapse" && finding.card_id && <button onClick={() => void decideFinding(finding.id, "accepted")}>Count as lapse</button>}
                {finding.kind === "first big mistake" && !cardPreviews[finding.id] && <button onClick={() => void createFindingCard(finding.id, false)}>Preview study card</button>}
                {finding.kind === "first big mistake" && cardPreviews[finding.id] && <>
                  <small>{cardPreviews[finding.id].starting_fen} · {cardPreviews[finding.id].moves.join(" ")}</small>
                  {selected && <button onClick={() => onAnalyze(selected, finding.ply)}>Edit position in Builder</button>}
                  <button onClick={() => void createFindingCard(finding.id, true)}>{cardPreviews[finding.id].existing_card_id ? "Use existing card" : "Save card due today"}</button>
                </>}
                <button onClick={() => void decideFinding(finding.id, "ignored")}>Ignore</button>
              </article>
            ))}
          </div>
          <div data-task="Library"><div className="game-filters">
            {(["source", "status", "color", "speed", "result"] as const).map(
              (field) => (
                <select
                  key={field}
                  aria-label={`Filter ${field}`}
                  value={filters[field]}
                  onChange={(event) => {
                    setPageCursor(null); setCursorHistory([]);
                    setFilters((current) => ({ ...current, [field]: event.target.value }));
                  }}
                >
                  <option value="All">All {field}</option>
                  {[...new Set(records.map((game) => game[field]))].map(
                    (value) => (
                      <option key={value}>{value}</option>
                    ),
                  )}
                </select>
              ),
            )}
            <input
              aria-label="Games since"
              type="date"
              value={filters.from}
              onChange={(event) =>
                { setPageCursor(null); setCursorHistory([]); setFilters((current) => ({
                  ...current, from: event.target.value,
                })); }
              }
            />
          </div>
          <section className="game-list">
            {games.map((game) => (
              <button
                className={`game-row${selected?.id === game.id ? " selected" : ""}`}
                key={game.id}
                onClick={() => {
                  setSelectedId(game.id);
                  setCursor(game.flagPly);
                }}
              >
                <span>
                  <b>{game.opening}</b>
                  <small>
                    {game.source} · {game.date} · {game.speed} · {game.color} ·{" "}
                    {game.result}
                  </small>
                </span>
                <span>
                  <em>{game.status}</em>
                  <small>{game.detail}</small>
                </span>
              </button>
            ))}
            {!games.length && (
              <div className="games-empty">
                <strong>No matching games.</strong>
                <button onClick={onSettings}>Set game accounts</button>
              </div>
            )}
          </section>
          <div className="pagination" aria-label="Game pages">
            <button disabled={!cursorHistory.length} onClick={() => {
              const previous = cursorHistory[cursorHistory.length - 1] ?? null;
              setCursorHistory((history) => history.slice(0, -1));
              setPageCursor(previous);
            }}>Previous games</button>
            <button disabled={!nextPageCursor} onClick={() => {
              setCursorHistory((history) => [...history, pageCursor]);
              setPageCursor(nextPageCursor);
            }}>Next games</button>
          </div>
          </div>
        </div>
      </div>}
    </section>
  );
}
