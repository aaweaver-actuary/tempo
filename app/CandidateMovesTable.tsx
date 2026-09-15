"use client";
import type { EngineMove } from "./lib/analysis-engines";
import type { ExplorerMove } from "./types";

//
function CandidateMovesTable({
  moves,
  covered,
  detail,
  onPlay,
  onHover,
}: {
  moves: Array<EngineMove & Partial<ExplorerMove>>;
  covered: Set<string>;
  detail: "probability" | "score" | "results";
  onPlay?: (uci: string) => void;
  onHover?: (uci: string | null) => void;
}) {
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
              ? result
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
            <strong>{move.san}</strong>
            <small>{value}</small>
            <em className={covered.has(move.uci) ? "covered" : "gap"}>
              {detail === "results" && games
                ? games.toLocaleString()
                : covered.has(move.uci)
                  ? "Covered"
                  : "Gap"}
            </em>
          </button>
        );
      })}
    </div>
  );
}
