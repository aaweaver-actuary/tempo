import { useState } from "react";
import type { CandidateMove } from "../domain";

type Column =
  | "Move"
  | "Repertoire"
  | "Stockfish"
  | "Maia"
  | "Lichess"
  | "Masters";
const columns: Column[] = [
  "Move",
  "Repertoire",
  "Stockfish",
  "Maia",
  "Lichess",
  "Masters",
];
type DatabaseSource = "Lichess" | "Masters";
function gamesFor(move?: CandidateMove) {
  return move
    ? (move.white ?? 0) + (move.draws ?? 0) + (move.black ?? 0)
    : undefined;
}
function engineQuality(move?: CandidateMove) {
  if (move?.mate !== undefined)
    return Math.sign(move.mate || 1) * (1_000_000 - Math.abs(move.mate));
  if (move?.cp !== undefined) return move.cp;
  const parsed = Number.parseFloat(move?.score ?? "");
  return Number.isFinite(parsed) ? parsed * 100 : undefined;
}

export function MoveComparisonTable({
  repertoire,
  engine,
  lichess,
  masters,
  maia,
  turn,
  onPlay,
  onHover,
}: {
  repertoire: CandidateMove[];
  engine: CandidateMove[];
  lichess: CandidateMove[];
  masters: CandidateMove[];
  maia: CandidateMove[];
  turn: "white" | "black";
  onPlay: (uci: string) => void;
  onHover: (uci: string | null) => void;
}) {
  const [sort, setSort] = useState<{
    column: Column;
    direction: "ascending" | "descending";
  }>();
  const [detail, setDetail] = useState<{
    source: DatabaseSource;
    uci: string;
  }>();
  const byMove = (moves: CandidateMove[]) =>
    new Map(moves.map((move) => [move.uci, move]));
  const sources = {
    Stockfish: byMove(engine),
    Maia: byMove(maia),
    Lichess: byMove(lichess),
    Masters: byMove(masters),
  };
  const covered = new Set(repertoire.map((move) => move.uci));
  const moves = [
    ...byMove([
      ...repertoire,
      ...engine,
      ...maia.slice(0, 5),
      ...lichess.slice(0, 5),
      ...masters.slice(0, 5),
    ]).values(),
  ];
  const sortValue = (move: CandidateMove): string | number | undefined => {
    switch (sort?.column) {
      case "Move":
        return move.san ?? move.uci;
      case "Repertoire":
        return covered.has(move.uci) ? 1 : 0;
      case "Stockfish":
        return engineQuality(sources.Stockfish.get(move.uci));
      case "Maia":
        return sources.Maia.get(move.uci)?.probability;
      case "Lichess":
        return gamesFor(sources.Lichess.get(move.uci));
      case "Masters":
        return gamesFor(sources.Masters.get(move.uci));
    }
  };
  if (sort)
    moves.sort((left, right) => {
      const a = sortValue(left),
        b = sortValue(right);
      if (a === undefined) return b === undefined ? 0 : 1;
      if (b === undefined) return -1;
      const compared =
        typeof a === "string" && typeof b === "string"
          ? a.localeCompare(b)
          : Number(a) - Number(b);
      return sort.direction === "ascending" ? compared : -compared;
    });
  const databaseCell = (source: DatabaseSource, candidate: CandidateMove) => {
    const move = sources[source].get(candidate.uci);
    if (!move) return "—";
    const games = gamesFor(move) ?? 0;
    const population = (source === "Lichess" ? lichess : masters).reduce(
      (sum, item) => sum + (gamesFor(item) ?? 0),
      0,
    );
    const percent = (count: number) =>
      `${Math.round(games ? (count / games) * 100 : 0)}%`;
    const score =
      (turn === "white" ? (move.white ?? 0) : (move.black ?? 0)) +
      (move.draws ?? 0) / 2;
    const expanded = detail?.source === source && detail.uci === candidate.uci;
    return (
      <div className="database-cell">
        <button
          aria-label={`${source} details for ${candidate.san ?? candidate.uci}`}
          aria-expanded={expanded}
          onClick={() =>
            setDetail(expanded ? undefined : { source, uci: candidate.uci })
          }
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setDetail(undefined);
            }
          }}
        >
          <span>{games.toLocaleString()}</span>
          <small>{percent(score)}</small>
        </button>
        {expanded && (
          <div
            className="database-detail"
            role="group"
            aria-label={`${source} results`}
          >
            <strong>
              {games.toLocaleString()} games · {percent(score)} {turn} score
            </strong>
            <span>
              White {percent(move.white ?? 0)} · Draw {percent(move.draws ?? 0)}{" "}
              · Black {percent(move.black ?? 0)}
            </span>
            <span>
              {Math.round((games / Math.max(1, population)) * 100)}% frequency
            </span>
            <button
              aria-label="Close results"
              onClick={() => setDetail(undefined)}
            >
              ×
            </button>
          </div>
        )}
      </div>
    );
  };
  return (
    <div className="move-comparison-scroll">
      <table aria-label="Move source comparison">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                scope="col"
                aria-sort={sort?.column === column ? sort.direction : "none"}
              >
                <button
                  onClick={() =>
                    setSort((previous) => ({
                      column,
                      direction:
                        previous?.column === column &&
                        previous.direction === "ascending"
                          ? "descending"
                          : "ascending",
                    }))
                  }
                >
                  {column}
                  <span aria-hidden="true">
                    {sort?.column === column
                      ? sort.direction === "ascending"
                        ? " ↑"
                        : " ↓"
                      : ""}
                  </span>
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {moves.map((move) => {
            const engineMove = sources.Stockfish.get(move.uci),
              maiaMove = sources.Maia.get(move.uci);
            return (
              <tr
                key={move.uci}
                onMouseEnter={() => onHover(move.uci)}
                onMouseLeave={() => onHover(null)}
              >
                <th scope="row">
                  <button
                    onClick={() => onPlay(move.uci)}
                    onFocus={() => onHover(move.uci)}
                    onBlur={() => onHover(null)}
                  >
                    {move.san ?? move.uci}
                  </button>
                </th>
                <td className="comparison-covered">
                  {covered.has(move.uci) ? "Covered" : "Gap"}
                </td>
                <td>
                  {engineMove?.score ??
                    (engineMove?.mate !== undefined
                      ? `Mate ${engineMove.mate}`
                      : engineMove?.cp !== undefined
                        ? (engineMove.cp / 100).toFixed(2)
                        : "—")}
                </td>
                <td>
                  {maiaMove?.probability !== undefined
                    ? `${Math.round(maiaMove.probability * 100)}%`
                    : "—"}
                </td>
                <td>{databaseCell("Lichess", move)}</td>
                <td>{databaseCell("Masters", move)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!moves.length && (
        <p className="panel-message">
          No moves from the enabled sources at this position.
        </p>
      )}
    </div>
  );
}
