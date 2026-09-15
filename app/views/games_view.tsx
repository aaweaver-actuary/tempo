"use client";
import { useState, useMemo, useCallback, useEffect } from "react";
import {
  type BoardTheme,
  type PieceSet,
  Chessboard,
} from "../components/chessboard";
import { STANDARD_FEN } from "../const";
import { analyzeWithStockfish } from "../lib/analysis-engines";
import { importGameAndReformatToGameViewRecord } from "../utils/pgn";
import { fenAfterMoves } from "../utils/fen";
import { convertSanToUci } from "../utils/chess";
import type { GameViewRecord } from "../types";
import { sampleGames } from "../samples";

export function GamesView({
  onAnalyze,
  theme,
  pieceSet,
}: {
  onAnalyze: () => void;
  theme: BoardTheme;
  pieceSet: PieceSet;
}) {
  const [lichess, setLichess] = useState(() =>
    typeof window === "undefined"
      ? ""
      : (localStorage.getItem("tempo-lichess-username") ?? ""),
  );
  const [chesscom, setChesscom] = useState(() =>
    typeof window === "undefined"
      ? ""
      : (localStorage.getItem("tempo-chesscom-username") ?? ""),
  );
  const [source, setSource] = useState("All");
  const [status, setStatus] = useState("All");
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState(() =>
    typeof window === "undefined"
      ? "Last 90 days · rated blitz, rapid, and classical"
      : (localStorage.getItem("tempo-last-sync-note") ??
        "Last 90 days · rated blitz, rapid, and classical"),
  );
  const [lastSync, setLastSync] = useState(() =>
    typeof window === "undefined"
      ? ""
      : (localStorage.getItem("tempo-last-sync") ?? ""),
  );
  const localApi =
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1"].includes(location.hostname)
      ? (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000")
      : "";
  const demoRecords = useMemo(
    () =>
      sampleGames.map((game) => ({
        ...game,
        startFen: STANDARD_FEN,
        color: game.color === "black" ? "black" : "white",
      })),
    [],
  );
  const [records, setRecords] = useState<GameViewRecord[]>(
    demoRecords as GameViewRecord[],
  );
  const [selectedId, setSelectedId] = useState(demoRecords[0].id);
  const [cursor, setCursor] = useState(demoRecords[0].flagPly);
  const [engineOn, setEngineOn] = useState(true);
  const [engineText, setEngineText] = useState(
    "Select a flagged position to analyze.",
  );
  const [gamesLoaded, setGamesLoaded] = useState(false);
  const selected = records.find((game) => game.id === selectedId) ??
    records[0] ?? {
      id: "",
      source: "",
      date: "",
      speed: "",
      color: "white",
      result: "",
      opening: "No synced game selected",
      status: "",
      detail: "",
      flag: "Sync an account to review games.",
      flagPly: 0,
      moves: [],
      startFen: STANDARD_FEN,
    };
  const games = records.filter(
    (game) =>
      (source === "All" || game.source === source) &&
      (status === "All" || game.status === status),
  );
  const gameFen = fenAfterMoves(
    selected.moves,
    Math.min(cursor, selected.moves.length),
    selected.startFen,
  );
  const gameLast = cursor
    ? convertSanToUci(selected.moves, selected.startFen)[cursor - 1]
    : undefined;
  const applicable = records.filter(
    (game) => game.status !== "no applicable repertoire",
  );
  const covered = records.filter((game) => game.status === "covered").length;
  const opponentGaps = records.filter(
    (game) => game.status === "opponent repertoire gap",
  ).length;
  const deviations = records.filter(
    (game) => game.status === "player deviation",
  ).length;
  const noRepertoire = records.filter(
    (game) => game.status === "no applicable repertoire",
  ).length;

  const loadGames = useCallback(async () => {
    if (!localApi) return;
    try {
      const response = await fetch(`${localApi}/api/games/summary`);
      if (!response.ok) throw new Error();
      const body = (await response.json()) as {
        games: Record<string, unknown>[];
      };
      const loaded = body.games
        .map(importGameAndReformatToGameViewRecord)
        .filter((game): game is GameViewRecord => Boolean(game));
      setRecords(loaded);
      setSelectedId((current) =>
        loaded.some((game) => game.id === current)
          ? current
          : (loaded[0]?.id ?? ""),
      );
      setCursor(loaded[0]?.flagPly ?? 0);
      setGamesLoaded(true);
    } catch {
      setRecords([]);
      setSelectedId("");
      setCursor(0);
      setGamesLoaded(true);
    }
  }, [localApi]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadGames();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadGames]);

  function saveAccounts() {
    localStorage.setItem("tempo-lichess-username", lichess.trim());
    localStorage.setItem("tempo-chesscom-username", chesscom.trim());
    setSyncNote("Accounts saved locally");
  }
  const sync = useCallback(async () => {
    if (syncing) return;
    localStorage.setItem("tempo-lichess-username", lichess.trim());
    localStorage.setItem("tempo-chesscom-username", chesscom.trim());
    setSyncing(true);
    if (!localApi) {
      setSyncNote(
        "Live sync is available in local Tempo. This private Site keeps only the comparison preview.",
      );
      setSyncing(false);
      return;
    }
    try {
      const response = await fetch(`${localApi}/api/games/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lichess_username: lichess,
          chesscom_username: chesscom,
          days: 90,
          speeds: ["blitz", "rapid", "classical"],
          rated_only: true,
        }),
      });
      if (!response.ok) throw new Error();
      const data = (await response.json()) as { synced_at?: string; imported?: number };
      const time = new Date(data.synced_at ?? Date.now()).toISOString();
      setLastSync(time);
      localStorage.setItem("tempo-last-sync", time);
      const note = `${data.imported ?? 0} new games · local cache is up to date`;
      setSyncNote(note);
      localStorage.setItem("tempo-last-sync-note", note);
      await loadGames();
    } catch {
      setSyncNote(
        "Saved. Start the local service to sync live games; the comparison preview remains available.",
      );
    }
    setSyncing(false);
  }, [chesscom, lichess, loadGames, localApi, syncing]);
  useEffect(() => {
    const run = () => {
      if (
        (localStorage.getItem("tempo-lichess-username") ||
          localStorage.getItem("tempo-chesscom-username")) &&
        document.visibilityState === "visible"
      )
        void sync();
    };
    const timer = window.setInterval(run, 180000);
    window.addEventListener("focus", run);
    window.addEventListener("online", run);
    run();
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", run);
      window.removeEventListener("online", run);
    };
  }, [sync]); // sync once on mount and then while visible

  useEffect(() => {
    let active = true;
    const run = async () => {
      if (!engineOn || !selected.id) {
        setEngineText("Select a synced game to analyze.");
        return;
      }
      setEngineText("Analyzing selected position…");
      try {
        const lines = await analyzeWithStockfish(gameFen);
        if (active)
          setEngineText(
            lines[0] ? `${lines[0].san} · ${lines[0].score}` : "No line",
          );
      } catch {
        if (active) setEngineText("Local engine unavailable");
      }
    };
    void run();
    return () => {
      active = false;
    };
  }, [engineOn, gameFen, selected.id]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLSelectElement
      )
        return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setCursor((value) => Math.max(0, value - 1));
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setCursor((value) => Math.min(selected.moves.length, value + 1));
      }
      if (event.key === "Home") {
        event.preventDefault();
        setCursor(0);
      }
      if (event.key === "End") {
        event.preventDefault();
        setCursor(selected.moves.length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected.moves.length]);

  return (
    <section className="games-page" id="games">
      <div className="page-heading compact">
        <div>
          <h1>Games</h1>
          <p>
            {lastSync
              ? `Last synced at ${new Date(lastSync).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
              : "Automatic local sync checks every 3 minutes"}
          </p>
        </div>
        <button
          className="primary-button sync-button"
          onClick={() => void sync()}
          disabled={syncing}
        >
          {syncing && <i />}
          {syncing ? "Syncing games" : "↻ Sync games"}
        </button>
      </div>
      <div className="game-review">
        <div className="game-board">
          <Chessboard
            fen={gameFen}
            lastMove={
              gameLast
                ? [gameLast.slice(0, 2), gameLast.slice(2, 4)]
                : undefined
            }
            locked
            showHint={false}
            theme={theme}
            pieceSet={pieceSet}
            onMove={() => undefined}
          />
          <div className="board-tools">
            <button
              disabled={!selected.id || cursor === 0}
              onClick={() => setCursor(Math.max(0, cursor - 1))}
            >
              ← Back
            </button>
            <button
              disabled={!selected.id || cursor === selected.moves.length}
              onClick={() =>
                setCursor(Math.min(selected.moves.length, cursor + 1))
              }
            >
              Forward →
            </button>
            <button
              disabled={!selected.id}
              onClick={() => setCursor(selected.flagPly)}
            >
              ⚑ First mistake
            </button>
            <button
              className={engineOn ? "active" : ""}
              onClick={() => setEngineOn(!engineOn)}
            >
              Stockfish
            </button>
          </div>
        </div>
        <div className="game-side-scroll">
          <aside className="game-inspector">
            <span className="pill">Quick scan</span>
            <h2>{selected.opening}</h2>
            <strong>{selected.flag}</strong>
            <p>{engineText}</p>
            <div className="game-moves">
              {selected.moves.map((move, index) => (
                <button
                  className={`${index < cursor ? "shown" : ""}${index === selected.flagPly ? " flagged" : ""}`}
                  onClick={() => setCursor(index + 1)}
                  key={`${move}-${index}`}
                >
                  {index % 2 === 0 ? `${Math.floor(index / 2) + 1}.` : ""}
                  {move}
                </button>
              ))}
            </div>
            <button
              className="primary-button"
              disabled={!selected.id}
              onClick={onAnalyze}
            >
              Open gap in builder
            </button>
          </aside>
          <section className="account-strip">
            <label>
              <span>Lichess username</span>
              <input
                value={lichess}
                onChange={(e) => setLichess(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <label>
              <span>Chess.com username</span>
              <input
                value={chesscom}
                onChange={(e) => setChesscom(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <button onClick={saveAccounts}>Save locally</button>
            <small>{syncNote}</small>
          </section>
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
            <article>
              <span>Opponent gaps</span>
              <strong>{opponentGaps}</strong>
              <small>Missing opponent responses</small>
            </article>
            <article>
              <span>Your deviations</span>
              <strong>{deviations}</strong>
              <small>First off-repertoire moves</small>
            </article>
            <article>
              <span>No repertoire</span>
              <strong>{noRepertoire}</strong>
              <small>Games without an applicable line</small>
            </article>
          </div>
          <div className="games-workspace">
            <aside className="gap-list">
              <span>Recurring repairs</span>
              {records
                .filter(
                  (game) =>
                    game.status.includes("gap") ||
                    game.status.includes("deviation"),
                )
                .slice(0, 4)
                .map((game) => (
                  <button key={game.id} onClick={onAnalyze}>
                    <b>{game.opening}</b>
                    <small>{game.detail} · open builder</small>
                  </button>
                ))}
              {!records.some(
                (game) =>
                  game.status.includes("gap") ||
                  game.status.includes("deviation"),
              ) && <p>No recurring repairs yet.</p>}
            </aside>
            <section className="game-list">
              <div className="game-filters">
                <select
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                >
                  <option>All</option>
                  <option>Lichess</option>
                  <option>Chess.com</option>
                </select>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option>All</option>
                  <option>covered</option>
                  <option>opponent repertoire gap</option>
                  <option>player deviation</option>
                  <option>no applicable repertoire</option>
                </select>
                <span>90 days · rated · blitz / rapid / classical</span>
              </div>
              {games.map((game) => (
                <button
                  className={`game-row${selected.id === game.id ? " selected" : ""}`}
                  key={game.id}
                  onClick={() => {
                    setSelectedId(game.id);
                    setCursor(game.flagPly);
                  }}
                >
                  <span>
                    <b>{game.opening}</b>
                    <small>
                      {game.source} · {game.date} · {game.speed} · {game.color}{" "}
                      · {game.result}
                    </small>
                  </span>
                  <span>
                    <em className={game.status.replaceAll(" ", "-")}>
                      {game.status}
                    </em>
                    <small>{game.detail}</small>
                  </span>
                  <i>Review →</i>
                </button>
              ))}
              {gamesLoaded && !games.length && (
                <div className="games-empty">
                  <strong>No matching synced games.</strong>
                  <span>Add a username above or change the filters.</span>
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
    </section>
  );
}
