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
import { analyzeWithStockfish } from "../lib/analysis-engines";
import type { GameSyncState } from "../hooks/use-game-sync";
import { importGameAndReformatToGameViewRecord } from "../utils/pgn";
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

export default function GamesView({
  onAnalyze,
  onSettings,
  onSync,
  onRepair,
  syncState,
  theme,
  pieceSet,
  useSharedBoard = false,
}: {
  onAnalyze: (game: GameViewRecord, cursor: number) => void;
  onSettings: () => void;
  onSync: () => void;
  onRepair?: () => void;
  syncState: GameSyncState;
  theme: BoardTheme;
  pieceSet: PieceSet;
  useSharedBoard?: boolean;
}) {
  const local = usesLocalApi();
  const tools = useTaskTabs(["Moves", "Analysis", "Library"], "Moves", "tempo-games-tools");
  const [loaded, setLoaded] = useState(!local);
  const [libraryPage, setLibraryPage] = useState(0);
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
  const [motifRecommendations, setMotifRecommendations] = useState<Array<{ motif: string; miss_count: number; total_loss_cp: number; supporting_games: string[]; recommended_pack_id?: string | null }>>([]);
  const [cardPreviews, setCardPreviews] = useState<Record<string, { starting_fen: string; moves: string[]; best_move: string; existing_card_id?: string | null }>>({});
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
  const loadGames = useCallback(async () => {
    if (!local) return;
    try {
      const response = await readWorkspaceResponse(
        `${API_URL}/api/games/summary`,
      );
      if (!response.ok) throw new Error("Could not load your local games.");
      const body = (await response.json()) as {
        games: Record<string, unknown>[];
      };
      const loaded = body.games
        .map(importGameAndReformatToGameViewRecord)
        .filter((game): game is GameViewRecord => Boolean(game));
      setRecords(loaded);
      if (!loaded.some((game) => game.id === selectedIdRef.current)) {
        setSelectedId(loaded[0]?.id ?? "");
        setCursor(loaded[0]?.flagPly ?? 0);
      }
      setError("");
      setLoaded(true);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not load games.",
      );
    }
  }, [local]);
  useEffect(() => {
    if (syncState.lastSuccess) invalidateWorkspaceData();
    queueMicrotask(() => void loadGames());
  }, [loadGames, syncState.lastSuccess]);
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
    if (!local) return;
    const [findingResponse, insightResponse] = await Promise.all([
      fetch(`${API_URL}/api/game-findings?status=pending`),
      fetch(`${API_URL}/api/game-insights/motifs`),
    ]);
    if (findingResponse.ok) {
      const payload = (await findingResponse.json()) as { findings?: typeof findings };
      setFindings(payload.findings ?? []);
    }
    if (insightResponse.ok) {
      const payload = (await insightResponse.json()) as { recommendations?: typeof motifRecommendations };
      setMotifRecommendations(payload.recommendations ?? []);
    }
  }, [local]);
  useEffect(() => {
    queueMicrotask(() => void loadFindings());
    const timer = window.setInterval(() => void loadFindings(), 15_000);
    return () => window.clearInterval(timer);
  }, [loadFindings, syncState.lastSuccess]);
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
  async function activateRecommendedPack(packId: string) {
    const response = await fetch(`${API_URL}/api/tactics/activation`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pack_ids: [packId], active: true }),
    });
    if (!response.ok) setError("Could not activate the recommended tactics pack.");
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
  useEffect(() => {
    let active = true;
    if (!engineOn || !selected) return;
    queueMicrotask(() => {
      if (active) setEngineText("Analyzing…");
    });
    void analyzeWithStockfish(gameFen, 12)
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
    return () => {
      active = false;
    };
  }, [engineOn, gameFen, selected]);
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
      fen: gameFen,
      lastMove: gameLast
        ? ([gameLast.slice(0, 2), gameLast.slice(2, 4)] as readonly [
            string,
            string,
          ])
        : undefined,
      shapes,
      interactionMode: "readonly",
      showHint: false,
      theme,
      pieceSet,
      orientation: selected?.color === "black" ? "black" : "white",
      positionRevision: cursor,
      onMove: undefined,
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
    gameFen,
    gameLast,
    pieceSet,
    releaseShellBoardForOwner,
    selected?.color,
    setShellBoardForOwner,
    shapes,
    theme,
    useSharedBoard,
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
              fen={gameFen}
              lastMove={
                gameLast
                  ? [gameLast.slice(0, 2), gameLast.slice(2, 4)]
                  : undefined
              }
              shapes={shapes}
              locked
              showHint={false}
              theme={theme}
              pieceSet={pieceSet}
              onMove={() => undefined}
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
            {motifRecommendations.map((recommendation) => (
              <article key={recommendation.motif}>
                <span>Work on {recommendation.motif}</span>
                <strong>{recommendation.miss_count} misses</strong>
                <small>{recommendation.total_loss_cp} total centipawns · {recommendation.supporting_games.length} games</small>
                {recommendation.recommended_pack_id && <button onClick={() => void activateRecommendedPack(recommendation.recommended_pack_id!)}>Activate next pack</button>}
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
                  onChange={(event) => { setLibraryPage(0); setFilters((current) => ({ ...current, [field]: event.target.value })); }}
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
                setFilters((current) => ({
                  ...current,
                  from: event.target.value,
                }))
              }
            />
          </div>
          <section className="game-list">
            {games.slice(libraryPage * 50, (libraryPage + 1) * 50).map((game) => (
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
          <div className="pagination" aria-label="Game pages"><button disabled={libraryPage === 0} onClick={() => setLibraryPage(value => value - 1)}>Previous games</button><button disabled={(libraryPage + 1) * 50 >= games.length} onClick={() => setLibraryPage(value => value + 1)}>Next games</button></div>
          </div>
        </div>
      </div>}
    </section>
  );
}
