import { useEffect, useState } from "react";
import { API_URL } from "../const";
import {
  chessStatisticsBreakdownSchema,
  chessStatisticsOverviewSchema,
} from "../domain/schemas";
import { readWorkspaceData } from "../lib/workspace-data";

type Overview = ReturnType<typeof chessStatisticsOverviewSchema.parse>;
type Breakdown = ReturnType<typeof chessStatisticsBreakdownSchema.parse>;
type WindowDays = 7 | 30 | 90 | 36500;
type Dimension = Breakdown["dimension"];

const percentage = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;

export default function StatisticsView() {
  const [windowDays, setWindowDays] = useState<WindowDays>(30);
  const [dimension, setDimension] = useState<Dimension>("color");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setError("");
    void Promise.all([
      readWorkspaceData(
        `${API_URL}/api/statistics/overview?window_days=${windowDays}`,
        chessStatisticsOverviewSchema,
      ),
      readWorkspaceData(
        `${API_URL}/api/statistics/breakdown?dimension=${dimension}&window_days=${windowDays}`,
        chessStatisticsBreakdownSchema,
      ),
    ]).then(([nextOverview, nextBreakdown]) => {
      if (!active) return;
      setOverview(nextOverview);
      setBreakdown(nextBreakdown);
    }).catch((reason) => {
      if (active) setError(reason instanceof Error ? reason.message : "Statistics unavailable.");
    });
    return () => { active = false; };
  }, [windowDays, dimension]);

  return (
    <section className="library-page statistics-page">
      <header className="workspace-title">
        <div>
          <h1>Chess statistics</h1>
          <p>Game outcomes use all valid games. Engine metrics use analyzed games only.</p>
        </div>
        <div>
          <label>
            Window{" "}
            <select value={windowDays} onChange={(event) => setWindowDays(Number(event.target.value) as WindowDays)}>
              <option value={7}>7 days</option><option value={30}>30 days</option>
              <option value={90}>90 days</option><option value={36500}>Lifetime</option>
            </select>
          </label>
        </div>
      </header>
      {error && <p role="alert">{error}</p>}
      {!overview && !error && <p role="status">Loading chess statistics…</p>}
      {overview && (
        <>
          <div className="stats-grid">
            <article className="card">
              <h2>Game score</h2><strong>{percentage(overview.score.value)}</strong>
              <p>{overview.score.wins} W · {overview.score.draws} D · {overview.score.losses} L</p>
              <small>{overview.score.numerator} points / {overview.score.denominator} games</small>
            </article>
            <article className="card">
              <h2>Decision quality</h2>
              <strong>{overview.decision_quality.mean_loss_cp?.toFixed(0) ?? "—"} cp</strong>
              <p>{overview.decision_quality.major_mistakes_per_game?.toFixed(2) ?? "—"} major mistakes/game</p>
              <small>{overview.decision_quality.denominator} analyzed games</small>
            </article>
            <article className="card">
              <h2>Tactical performance</h2><strong>{percentage(overview.tactical_performance.value)}</strong>
              <p>{overview.tactical_performance.found} found / {overview.tactical_performance.opportunities} opportunities</p>
              <small>{overview.tactical_performance.conceded_per_100_decisions?.toFixed(1) ?? "—"} conceded / 100 decisions</small>
            </article>
          </div>
          <p>{overview.analyzed_games} of {overview.games} games analyzed ({percentage(overview.analysis_coverage)} coverage).</p>
        </>
      )}
      <section className="card">
        <h2>Breakdown</h2>
        <label>Compare by{" "}
          <select value={dimension} onChange={(event) => setDimension(event.target.value as Dimension)}>
            <option value="color">Color</option><option value="speed">Speed</option>
            <option value="provider">Provider</option><option value="opponent_rating">Opponent rating</option>
            <option value="relative_rating">Relative rating</option><option value="weekday">Day of week</option>
            <option value="hour">Hour</option><option value="opening">Opening</option>
            <option value="repertoire">Repertoire</option>
          </select>
        </label>
        {breakdown && (
          <table>
            <caption>Performance by {breakdown.dimension.replaceAll("_", " ")}</caption>
            <thead><tr><th>Segment</th><th>Games</th><th>Score</th><th>Mean loss</th><th>Tactics</th></tr></thead>
            <tbody>{breakdown.segments.map((segment) => (
              <tr key={segment.segment}><th>{segment.segment}</th><td>{segment.games}</td>
                <td>{percentage(segment.score)}</td><td>{segment.mean_loss_cp?.toFixed(0) ?? "—"} cp</td>
                <td>{segment.tactical_found}/{segment.tactical_opportunities}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <details><summary>Methodology</summary>
        <p>Score = (wins + 0.5 × draws) / games. Decision quality excludes unanalyzed games. Tactical rate includes only high-confidence opportunities. Opening exit is the first out-of-repertoire position, or ply 20; endgame entry uses N/B=1, R=2, Q=4 and begins at a total phase score of six.</p>
      </details>
    </section>
  );
}
