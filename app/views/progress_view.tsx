"use client";
export default function ProgressView({
  reviewed,
  cardsLeft,
  totalCards,
}: {
  reviewed: number;
  cardsLeft: number;
  totalCards: number;
}) {
  const days = [
    ["Thu", 16],
    ["Fri", 22],
    ["Sat", 8],
    ["Sun", 28],
    ["Mon", 19],
    ["Tue", 31],
    ["Today", Math.max(8, reviewed)],
  ];
  return (
    <section className="progress-page" id="progress">
      <div className="page-heading compact">
        <div>
          <p className="eyebrow">Quiet consistency</p>
          <h1>Progress</h1>
          <p>
            Review volume matters less than returning when each card is due.
          </p>
        </div>
        <span className="streak">12 day streak</span>
      </div>
      <div className="metric-grid">
        <article>
          <span>Due today</span>
          <strong>{cardsLeft}</strong>
          <small>
            {cardsLeft ? "Continue today’s queue" : "Queue complete"}
          </small>
        </article>
        <article>
          <span>Cards available</span>
          <strong>{totalCards}</strong>
          <small>Across local repertoires and examples</small>
        </article>
        <article>
          <span>Reviewed today</span>
          <strong>{reviewed}</strong>
          <small>Completed attempts</small>
        </article>
        <article>
          <span>Clean passes</span>
          <strong>
            {typeof window === "undefined"
              ? 0
              : JSON.parse(
                  localStorage.getItem("tempo-first-clean-passes") ?? "[]",
                ).length}
          </strong>
          <small>Cards recalled without guidance</small>
        </article>
      </div>
      <div className="analytics-grid">
        <article className="chart-card">
          <div className="chart-heading">
            <div>
              <span>Review activity</span>
              <strong>{reviewed} cards today</strong>
            </div>
            <small>7 days</small>
          </div>
          <div className="bar-chart">
            {days.map(([label, value]) => (
              <div className="bar-column" key={label}>
                <span style={{ height: `${Number(value) * 3.3}px` }} />
                <small>{label}</small>
              </div>
            ))}
          </div>
        </article>
        <article className="maturity-card">
          <span>Maturity</span>
          <h2>Most of your repertoire is becoming stable.</h2>
          <div className="donut">
            <strong>72%</strong>
            <small>learning or mature</small>
          </div>
          <ul>
            <li>
              <i className="new" />
              New <strong>96</strong>
            </li>
            <li>
              <i className="learning" />
              Learning <strong>88</strong>
            </li>
            <li>
              <i className="mature" />
              Mature <strong>184</strong>
            </li>
          </ul>
        </article>
      </div>
    </section>
  );
}
