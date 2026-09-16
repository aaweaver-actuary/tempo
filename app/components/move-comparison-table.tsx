import type { CandidateMove } from "../domain";

function gameDetails(move: CandidateMove | undefined, population: number, turn: "white" | "black") {
  if (!move) return "—";
  const games = (move.white ?? 0) + (move.draws ?? 0) + (move.black ?? 0);
  if (!games) return "0 games";
  const percent = (count: number) => `${Math.round(count / games * 100)}%`;
  const score = (turn === "white" ? move.white ?? 0 : move.black ?? 0) + (move.draws ?? 0) / 2;
  return `${games.toLocaleString()} games · ${Math.round(games / Math.max(1, population) * 100)}% frequency · W ${percent(move.white ?? 0)} / D ${percent(move.draws ?? 0)} / B ${percent(move.black ?? 0)} · ${percent(score)} score`;
}

export function MoveComparisonTable({ repertoire, engine, lichess, masters, maia, turn, onPlay, onHover }: {
  repertoire: CandidateMove[]; engine: CandidateMove[]; lichess: CandidateMove[]; masters: CandidateMove[]; maia: CandidateMove[];
  turn: "white" | "black"; onPlay: (uci: string) => void; onHover: (uci: string | null) => void;
}) {
  const moves = [...new Map([...repertoire, ...engine, ...maia.slice(0, 5), ...lichess.slice(0, 5), ...masters.slice(0, 5)].map(move => [move.uci, move])).values()];
  const population = (source: CandidateMove[]) => source.reduce((sum, move) => sum + (move.white ?? 0) + (move.draws ?? 0) + (move.black ?? 0), 0);
  return <div className="move-comparison-scroll"><table aria-label="Move source comparison">
    <thead><tr><th>Move</th><th>Repertoire</th><th>Stockfish</th><th>Maia</th><th>Lichess</th><th>Masters</th></tr></thead>
    <tbody>{moves.map(move => {
      const engineMove = engine.find(candidate => candidate.uci === move.uci);
      const maiaMove = maia.find(candidate => candidate.uci === move.uci);
      return <tr key={move.uci} onMouseEnter={() => onHover(move.uci)} onMouseLeave={() => onHover(null)}>
        <th><button onClick={() => onPlay(move.uci)} onFocus={() => onHover(move.uci)} onBlur={() => onHover(null)}>{move.san ?? move.uci}</button></th>
        <td className="comparison-covered">{repertoire.some(candidate => candidate.uci === move.uci) ? "Covered" : "Gap"}</td>
        <td>{engineMove?.score ?? (engineMove?.mate !== undefined ? `Mate ${engineMove.mate}` : engineMove?.cp !== undefined ? (engineMove.cp / 100).toFixed(2) : "—")}</td>
        <td>{maiaMove ? `${Math.round((maiaMove.probability ?? 0) * 100)}%` : "—"}</td>
        <td>{gameDetails(lichess.find(candidate => candidate.uci === move.uci), population(lichess), turn)}</td>
        <td>{gameDetails(masters.find(candidate => candidate.uci === move.uci), population(masters), turn)}</td>
      </tr>;
    })}</tbody>
  </table>{!moves.length && <p className="panel-message">Enable a source above, or play a repertoire move to compare this position.</p>}</div>;
}
