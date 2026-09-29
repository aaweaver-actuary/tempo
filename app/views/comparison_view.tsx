"use client";

import { useEffect, useMemo, useState } from "react";
import { Chess, type Square } from "chess.js";
import type { DrawShape } from "@lichess-org/chessground/draw";
import { Button } from "../components/buttons/BaseButton";
import { Chessboard, type BoardTheme, type PieceSet } from "../components/chessboard";
import { API_URL } from "../const";
import { runStudyTask } from "../lib/background-study";
import {
  comparisonCardsSchema,
  comparisonFen,
  comparisonPieceDifferences,
  replayComparisonRoute,
  type ComparisonBoard,
  type ComparisonCard,
  type ComparisonLaunch,
  type ComparisonPosition,
} from "../lib/comparison";

let nextComparisonRevision = 0;
const SESSION_KEY = "tempo-comparison-session";

function savedBoards(launch: ComparisonLaunch): ComparisonBoard[] {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return [launch.source];
    const saved = JSON.parse(raw) as { sourceKey?: string; boards?: ComparisonBoard[] };
    if (saved.sourceKey !== launch.sourceKey || !Array.isArray(saved.boards) ||
        saved.boards.length < 1 || saved.boards.length > 4) return [launch.source];
    for (const board of saved.boards) {
      new Chess(comparisonFen(board));
      if (board.cursor < 0 || board.cursor > board.history.length) return [launch.source];
    }
    return saved.boards;
  } catch { return [launch.source]; }
}

function canonicalPosition(fen: string): string {
  return fen.split(" ").slice(0, 4).join(" ");
}

function sanForNext(fen: string, uci: string | undefined): string {
  if (!uci) return "Endpoint";
  try {
    return new Chess(fen).move({
      from: uci.slice(0, 2) as Square,
      to: uci.slice(2, 4) as Square,
      promotion: uci[4] || undefined,
    }).san;
  } catch { return uci; }
}

export default function ComparisonView({
  launch, theme, pieceSet, onReturn,
}: {
  launch: ComparisonLaunch;
  theme: BoardTheme;
  pieceSet: PieceSet;
  onReturn: () => void;
}) {
  const [boards, setBoards] = useState<ComparisonBoard[]>(() => savedBoards(launch));
  const [selectedBoardId, setSelectedBoardId] = useState(boards[0].id);
  const [cards, setCards] = useState<ComparisonCard[]>([]);
  const [revision, setRevision] = useState<number>();
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [matchResult, setMatchResult] = useState<{ key: string; positions: ComparisonPosition[] }>();
  const [matchError, setMatchError] = useState<{ key: string; message: string }>();
  const [showAllRepertoires, setShowAllRepertoires] = useState(!launch.repertoireId);
  const [showAllMatches, setShowAllMatches] = useState(false);
  const [routeLimits, setRouteLimits] = useState<Record<string, number>>({});
  const [orientationByBoard, setOrientationByBoard] = useState<Record<string, "white" | "black">>({});
  const selectedBoard = boards.find((board) => board.id === selectedBoardId) ?? boards[0];
  const selectedFen = comparisonFen(selectedBoard);
  const queryKey = `${revision}:${selectedFen}:${showAllRepertoires ? "all" : launch.repertoireId ?? "all"}`;
  const matches = useMemo(
    () => matchResult?.key === queryKey ? matchResult.positions : [],
    [matchResult, queryKey],
  );
  const searchPending = revision !== undefined &&
    matchResult?.key !== queryKey && matchError?.key !== queryKey;
  const cardsById = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);

  useEffect(() => {
    try {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify({ sourceKey: launch.sourceKey, boards }));
    } catch {
      // Comparison remains usable when browser storage is disabled.
    }
  }, [boards, launch.sourceKey]);

  useEffect(() => {
    const controller = new AbortController();
    const currentRevision = ++nextComparisonRevision;
    queueMicrotask(() => {
      if (controller.signal.aborted) return;
      setCards([]);
      setRevision(undefined);
      setLoadError("");
    });
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/api/compare/cards`, { signal: controller.signal });
        if (!response.ok) throw new Error(`Service returned HTTP ${response.status}.`);
        const payload = comparisonCardsSchema.parse(await response.json());
        await runStudyTask({
          kind: "initializeComparisonIndex", revision: currentRevision, cards: payload.cards,
        }, controller.signal);
        if (!controller.signal.aborted) {
          setCards(payload.cards);
          setRevision(currentRevision);
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setLoadError(error instanceof Error ? error.message : "Could not load saved cards.");
      }
    })();
    return () => {
      controller.abort();
      void runStudyTask({ kind: "releaseComparisonIndex", revision: currentRevision })
        .catch(() => undefined);
    };
  }, [reload]);

  useEffect(() => {
    if (revision === undefined) return;
    const controller = new AbortController();
    void runStudyTask<ComparisonPosition[]>({
      kind: "findComparisonMatches", revision, fen: selectedFen,
      repertoireId: showAllRepertoires ? undefined : launch.repertoireId,
    }, controller.signal).then((found) => {
      if (!controller.signal.aborted) setMatchResult({ key: queryKey, positions: found });
    }).catch((error) => {
      if (!controller.signal.aborted) setMatchError({ key: queryKey, message: String(error) });
    });
    return () => controller.abort();
  }, [revision, selectedFen, showAllRepertoires, launch.repertoireId, queryKey]);

  const selectedCard = selectedBoard.cardId ? cardsById.get(selectedBoard.cardId) : undefined;
  const selectedNextMove = selectedCard && selectedBoard.history.slice(0, selectedBoard.cursor)
    .every((move, index) => move.uci === selectedCard.moves[index])
    ? selectedCard.moves[selectedBoard.cursor] : undefined;
  const groupedMatches = useMemo(() => {
    const groups = new Map<string, ComparisonPosition[]>();
    for (const match of matches) {
      if (match.cardId === selectedBoard.cardId && match.ply === selectedBoard.cursor &&
          canonicalPosition(match.fen) === canonicalPosition(selectedFen)) continue;
      const key = canonicalPosition(match.fen);
      const routes = groups.get(key) ?? [];
      routes.push(match);
      groups.set(key, routes);
    }
    return [...groups.values()].sort((left, right) => {
      const leftDifferent = left.some((route) =>
        selectedNextMove && route.nextUci && route.nextUci !== selectedNextMove);
      const rightDifferent = right.some((route) =>
        selectedNextMove && route.nextUci && route.nextUci !== selectedNextMove);
      const leftRank = left[0].distance === 0 ? 0 : leftDifferent ? 1 : 2;
      const rightRank = right[0].distance === 0 ? 0 : rightDifferent ? 1 : 2;
      return leftRank - rightRank || left[0].distance - right[0].distance ||
        left[0].fen.localeCompare(right[0].fen);
    });
  }, [matches, selectedBoard.cardId, selectedBoard.cursor, selectedFen, selectedNextMove]);

  function changeBoard(id: string, update: (board: ComparisonBoard) => ComparisonBoard) {
    setBoards((current) => current.map((board) => board.id === id ? update(board) : board));
  }

  function playMove(id: string, from: Square, to: Square) {
    changeBoard(id, (board) => {
      const position = new Chess(comparisonFen(board));
      try {
        const move = position.move({ from, to, promotion: "q" });
        const uci = `${move.from}${move.to}${move.promotion ?? ""}`;
        return {
          ...board,
          history: [...board.history.slice(0, board.cursor), { uci, san: move.san, fen: position.fen() }],
          cursor: board.cursor + 1,
        };
      } catch { return board; }
    });
  }

  function pinPosition(position: ComparisonPosition) {
    const card = cardsById.get(position.cardId);
    if (!card || boards.length >= 4) return;
    try {
      const history = replayComparisonRoute(card.start_fen, card.moves);
      const board: ComparisonBoard = {
        id: `${card.id}:${position.ply}`,
        label: card.repertoires.map((repertoire) => repertoire.name).join(" / ") || "Repertoire card",
        cardId: card.id, orientation: card.trained_color === "black" ? "black" : "white",
        startingFen: card.start_fen, history, cursor: position.ply,
      };
      if (!boards.some((pinned) => pinned.id === board.id)) setBoards((current) => [...current, board]);
    } catch { setMatchError({ key: queryKey, message: "That card has an invalid move route. Refresh or repair the card." }); }
  }

  return (
    <section className="comparison-workspace" aria-label="Compare positions">
      <header className="comparison-header">
        <div><h1>Compare positions</h1><p>Step through saved cards and compare the exact decisions.</p></div>
        <div className="comparison-header-actions">
          <Button onClick={() => { setBoards([launch.source]); setSelectedBoardId(launch.source.id); }}>Clear pins</Button>
          <Button onClick={onReturn}>Return to {launch.returnView === "train" ? "Training" : "Builder"}</Button>
        </div>
      </header>
      {loadError && <div className="ui-notice error" role="alert">Cards could not be loaded. {loadError} <Button onClick={() => setReload((value) => value + 1)}>Retry</Button></div>}
      {!loadError && revision === undefined && <p role="status">Loading saved cards…</p>}
      <div className="comparison-grid">
        {boards.map((board) => {
          const fen = comparisonFen(board);
          const card = board.cardId ? cardsById.get(board.cardId) : undefined;
          const nextUci = card && board.history.slice(0, board.cursor)
            .every((move, index) => move.uci === card.moves[index]) ? card.moves[board.cursor] : undefined;
          const differenceSquares = board.id === selectedBoard.id ? [] : comparisonPieceDifferences(selectedFen, fen);
          const shapes: DrawShape[] = differenceSquares.map((square) => ({ orig: square as Square, brush: "yellow" }));
          return <article key={board.id} className={`comparison-tile${board.id === selectedBoard.id ? " selected" : ""}`}>
            <div className="comparison-tile-heading">
              <div><strong>{board.label}</strong><small>{board.id === selectedBoard.id ? "Search source" : differenceSquares.length ? `Different squares: ${differenceSquares.join(", ")}` : "Same board position"}</small></div>
              <div>
                {board.id !== selectedBoard.id && <Button onClick={() => setSelectedBoardId(board.id)}>Search from here</Button>}
                {board.id !== launch.source.id && <Button aria-label={`Remove ${board.label}`} onClick={() => {
                  setBoards((current) => current.filter((item) => item.id !== board.id));
                  if (selectedBoardId === board.id) setSelectedBoardId(launch.source.id);
                }}>×</Button>}
              </div>
            </div>
            <Chessboard
              owner={`compare:${board.id}`} fen={fen} locked={false} showHint={false}
              theme={theme} pieceSet={pieceSet} shapes={shapes}
              orientation={orientationByBoard[board.id] ?? board.orientation ?? "white"}
              onMove={(from, to) => playMove(board.id, from, to)}
            />
            <div className="comparison-tile-controls">
              <Button onClick={() => changeBoard(board.id, (current) => ({ ...current, cursor: Math.max(0, current.cursor - 1) }))}>←</Button>
              <Button onClick={() => changeBoard(board.id, (current) => ({ ...current, cursor: Math.min(current.history.length, current.cursor + 1) }))}>→</Button>
              <Button onClick={() => setOrientationByBoard((current) => ({
                ...current,
                [board.id]: (current[board.id] ?? board.orientation ?? "white") === "black" ? "white" : "black",
              }))}>Flip</Button>
              <span>Move {board.cursor} · {nextUci ? `Next: ${sanForNext(fen, nextUci)}` : "No saved next move"}</span>
            </div>
            <div className="comparison-moves" aria-label={`${board.label} moves`}>
              <Button className={board.cursor === 0 ? "active" : ""} onClick={() => changeBoard(board.id, (current) => ({ ...current, cursor: 0 }))}>Start</Button>
              {board.history.map((move, index) => <Button key={`${move.uci}-${index}`} className={board.cursor === index + 1 ? "active" : ""} onClick={() => changeBoard(board.id, (current) => ({ ...current, cursor: index + 1 }))}>{index % 2 === 0 ? `${Math.floor(index / 2) + 1}. ` : "…"}{move.san}</Button>)}
            </div>
          </article>;
        })}
      </div>
      <section className="comparison-results" aria-label="Saved position matches">
        <div className="comparison-results-heading">
          <div><h2>Saved position matches</h2><p>Exact boards include move-order transpositions. Nearby boards differ by up to two piece relocations.</p></div>
          <label><input type="checkbox" checked={showAllRepertoires} onChange={(event) => setShowAllRepertoires(event.target.checked)} /> All repertoires</label>
        </div>
        {searchPending && <p role="status">Finding matching cards…</p>}
        {matchError?.key === queryKey && <p role="alert">Search failed. {matchError.message}</p>}
        {matchResult?.key === queryKey && !groupedMatches.length && <p>No saved card positions match this board.</p>}
        {(showAllMatches ? groupedMatches : groupedMatches.slice(0, 12)).map((routes) => <div className="comparison-match" key={canonicalPosition(routes[0].fen)}>
          <div><strong>{routes[0].distance === 0
            ? routes.some((route) => route.routeSan?.join(" ") !== selectedBoard.history.slice(0, selectedBoard.cursor).map((move) => move.san).join(" "))
              ? "Exact transposition" : "Exact position"
            : `${routes[0].distance} piece relocations`}</strong><small>{routes.length} saved {routes.length === 1 ? "route" : "routes"}</small></div>
          <div className="comparison-match-routes">{routes.slice(0, routeLimits[canonicalPosition(routes[0].fen)] ?? 8).map((route) => {
            const card = cardsById.get(route.cardId);
            const routeId = `${route.cardId}:${route.ply}`;
            return <Button key={routeId} disabled={boards.length >= 4 || boards.some((board) => board.id === routeId)} onClick={() => pinPosition(route)}>
              <span>{card?.repertoires.map((repertoire) => repertoire.name).join(" / ") ?? "Card"} · ply {route.ply} · {sanForNext(route.fen, route.nextUci)}</span>
              <small>{route.routeSan?.join(" ") || "Starting position"}</small>
            </Button>;
          })}</div>
          {routes.length > (routeLimits[canonicalPosition(routes[0].fen)] ?? 8) && <Button onClick={() => setRouteLimits((current) => ({
            ...current,
            [canonicalPosition(routes[0].fen)]: (current[canonicalPosition(routes[0].fen)] ?? 8) + 20,
          }))}>Show more routes</Button>}
        </div>)}
        {groupedMatches.length > 12 && <Button onClick={() => setShowAllMatches((value) => !value)}>{showAllMatches ? "Show fewer" : `Show all ${groupedMatches.length} positions`}</Button>}
      </section>
    </section>
  );
}
