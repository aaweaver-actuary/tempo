"use client";

import type { CandidateMove } from "./domain";
export default function CandidateMovesTable({
  moves,
  covered,
  detail,
  onPlay,
  onHover,
  turn = "white",
  totalGames,
}: {
  moves: CandidateMove[];
  covered: Set<string>;
  detail: "probability" | "score" | "results";
  onPlay?: (uci: string) => void;
  onHover?: (uci: string | null) => void;
  turn?: "white" | "black";
  totalGames?: number;
}) {
  if (!moves.length)
    return <p className="panel-message">No candidate moves found.</p>;
  const population =
    totalGames ??
    moves.reduce(
      (sum, move) =>
        sum + (move.white ?? 0) + (move.draws ?? 0) + (move.black ?? 0),
      0,
    );
  return (
    <div className="candidate-list">
      {moves.map((move, index) => {
        const games = (move.white ?? 0) + (move.draws ?? 0) + (move.black ?? 0);
        const result = games
          ? `${Math.round(((move.white ?? 0) / games) * 100)}W · ${Math.round(((move.draws ?? 0) / games) * 100)}D · ${Math.round(((move.black ?? 0) / games) * 100)}L`
          : "";
        const value =
          detail === "probability"
            ? `${Math.round((move.probability ?? 0) * 100)}%`
            : detail === "results"
              ? games.toLocaleString()
              : (move.score ?? "Repertoire");
        return (
          <button
            className="candidate-row"
            key={move.uci}
            onClick={() => onPlay?.(move.uci)}
            onMouseEnter={() => onHover?.(move.uci)}
            onMouseLeave={() => onHover?.(null)}
            onFocus={() => onHover?.(move.uci)}
            onBlur={() => onHover?.(null)}
          >
            <span>{index + 1}</span>
            <strong>{move.san ?? move.uci}</strong>
            <small>{value}</small>
            <em className={covered.has(move.uci) ? "covered" : "gap"}>
              {detail === "results" && games
                ? games.toLocaleString()
                : covered.has(move.uci)
                  ? "Covered"
                  : "Gap"}
            </em>
            {detail === "results" && games > 0 && (
              <span className="candidate-statistics">
                {Math.round((games / population) * 100)}% frequency · {result} ·{" "}
                {turn === "white" ? "White" : "Black"} score{" "}
                {Math.round(
                  (((turn === "white" ? (move.white ?? 0) : (move.black ?? 0)) +
                    (move.draws ?? 0) / 2) /
                    games) *
                    100,
                )}
                %
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
