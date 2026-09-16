"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DrawShape } from "@lichess-org/chessground/draw";
import {
  Chessboard,
  type BoardTheme,
  type PieceSet,
} from "../components/chessboard";
import { API_URL, STANDARD_FEN } from "../const";
import { readWorkspaceResponse, invalidateWorkspaceData } from "../lib/workspace-data";
import { analyzeWithStockfish } from "../lib/analysis-engines";
import { scanGame } from "../lib/game-scan";
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

export function GamesView({
  onAnalyze,
  onSettings,
  onSync,
  syncState,
  theme,
  pieceSet,
}: {
  onAnalyze: (game: GameViewRecord, cursor: number) => void;
  onSettings: () => void;
  onSync: () => void;
  syncState: GameSyncState;
  theme: BoardTheme;
  pieceSet: PieceSet;
}) {
  const local = usesLocalApi();
  const [records, setRecords] = useState<GameViewRecord[]>(() =>
    local ? [] : sampleGames,
  );
  const [selectedId, setSelectedId] = useState<GameId | "">("");
  const [cursor, setCursor] = useState(0);
  const selectedIdRef = useRef(selectedId);
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);
  const [engineOn, setEngineOn] = useState(
    () => localStorage.getItem("tempo-games-engine-on") !== "false",
  );
  const [engineText, setEngineText] = useState("");
  const [error, setError] = useState("");
  const [scanStatus, setScanStatus] = useState("");
  const [lines, setLines] = useState<AnalysisLine[]>([]);
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
  const indexTask = useMemo<StudyTask>(() => ({ kind: "index", lines }), [lines]);
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
      const response = await readWorkspaceResponse(`${API_URL}/api/games/summary`);
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
          body.lines.map((line: Record<string, unknown>) =>
            ({
              id: asLineId(String(line.id)),
              repertoireId: asRepertoireId(String(line.repertoire_id)),
              repertoireName: String(line.repertoire_name),
              title: String(line.name),
              side: line.trained_color === "black" ? "black" : "white",
              startingFen: asFenString(String(line.start_fen)),
              moves: (Array.isArray(line.moves) ? line.moves : []).map((move) =>
                asSanMove(String(move)),
              ),
            }),
          ),
        );
      })
      .catch(() => undefined);
  }, [local]);
  const pending = records.find((game) => game.analysisState === "pending");
  useEffect(() => {
    if (!local || !pending) return;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted)
        setScanStatus(`Scanning ${pending.opening}…`);
    });
    void scanGame(
      pending.startFen,
      pending.moves,
      pending.color,
      (fen) => analyzeWithStockfish(fen, 6),
      controller.signal,
    )
      .then(async (evaluations) => {
        if (controller.signal.aborted) return;
        const response = await fetch(
          `${API_URL}/api/games/${encodeURIComponent(pending.id)}/analysis`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ evaluations, depth: 6 }),
          },
        );
        if (!response.ok) throw new Error("Could not save the game scan.");
        invalidateWorkspaceData();
        await loadGames();
        setScanStatus("");
      })
      .catch((reason) => {
        if (!controller.signal.aborted)
          setScanStatus(
            `Game scan failed: ${reason instanceof Error ? reason.message : "retry by reopening Games"}`,
          );
      });
    return () => controller.abort();
  }, [local, pending, loadGames]);
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
  const applicable = games.filter((game) => game.status !== "no repertoire");
  const covered = applicable.filter((game) => game.status === "covered").length;
  return (
    <section className="games-page" id="games">
      <div className="page-heading compact">
        <div>
          <h1>Games{!local ? " · Demo" : ""}</h1>
          <p>
            {syncState.lastSuccess
              ? `Last sync ${new Date(syncState.lastSuccess).toLocaleString()}`
              : "No successful sync yet"}
          </p>
        </div>
        {local && (
          <button
            className="primary-button sync-button"
            onClick={onSync}
            disabled={syncState.syncing}
          >
            {syncState.syncing && <i />}
            {syncState.syncing ? "Syncing games" : "↻ Sync games"}
          </button>
        )}
      </div>
      {(error || syncState.error) && (
        <p role="alert">
          {error || syncState.error}{" "}
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
      <div className="game-review">
        <div className="game-board">
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
          <div className="board-tools">
            <button
              onClick={() => setCursor((value) => Math.max(0, value - 1))}
            >
              ← Back
            </button>
            <button
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
          </div>
        </div>
        <div className="game-side-scroll">
          <aside className="game-inspector">
            <h2>{selected?.opening ?? "No games imported"}</h2>
            <strong>{selected?.flag}</strong>
            {engineOn && <p>{engineText}</p>}
            {scanStatus && <p role="status">{scanStatus}</p>}
            <div className="game-moves">
              {selected?.moves.map((move, index) => (
                <button
                  className={`${index < cursor ? "shown" : ""}${index === selected.flagPly ? " flagged" : ""}`}
                  onClick={() => setCursor(index + 1)}
                  key={index}
                >
                  {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}
                  {move}
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
          <div className="games-metrics">
            <article>
              <span>Repertoire adherence</span>
              <strong>
                {applicable.length
                  ? `${Math.round((covered / applicable.length) * 100)}%`
                  : "—"}
              </strong>
              <small>
                {covered} of {applicable.length} applicable games
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
          </div>
          <div className="game-filters">
            {(["source", "status", "color", "speed", "result"] as const).map(
              (field) => (
                <select
                  key={field}
                  aria-label={`Filter ${field}`}
                  value={filters[field]}
                  onChange={(event) =>
                    setFilters((current) => ({
                      ...current,
                      [field]: event.target.value,
                    }))
                  }
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
        </div>
      </div>
    </section>
  );
}
