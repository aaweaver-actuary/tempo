"use client";
import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";

type Progress = {
  states: Record<string, number>;
  activity: { date: string; count: number }[];
  reviewedToday: number;
  cleanCards: number;
  dueToday: number;
  totalCards: number;
};

export default function ProgressView({ reviewed, cardsLeft, totalCards }: { reviewed: number; cardsLeft: number; totalCards: number }) {
  const [data, setData] = useState<Progress>({ states: {}, activity: [], reviewedToday: reviewed, cleanCards: 0, dueToday: cardsLeft, totalCards });
  const [error, setError] = useState("");
  useEffect(() => {
    if (!usesLocalApi()) return;
    let active = true;
    void fetch(`${API_URL}/api/progress`).then(async (response) => {
      if (!response.ok) throw new Error();
      const summary = await response.json() as Progress;
      if (active) setData(summary);
    }).catch(() => { if (active) setError("Could not load review history. Check the local service."); });
    return () => { active = false; };
  }, []);
  const maximum = Math.max(1, ...data.activity.map((day) => day.count));
  return <section className="progress-page" id="progress">
    <div className="page-heading compact"><h1>Progress</h1></div>
    {error && <p role="alert">{error}</p>}
    <div className="metric-grid">
      {[["Due today", data.dueToday], ["Cards available", data.totalCards], ["Reviewed today", data.reviewedToday], ["Cards recalled cleanly", data.cleanCards]].map(([label, count]) => <article key={label}><span>{label}</span><strong>{count}</strong></article>)}
    </div>
    <div className="analytics-grid">
      <article className="chart-card"><div className="chart-heading"><strong>Review activity</strong><small>Last 7 days</small></div>
        <div className="bar-chart">{data.activity.map((day) => <div className="bar-column" key={day.date}><b>{day.count}</b><span style={{ height: `${day.count / maximum * 130}px` }} /><small>{new Date(`${day.date}T12:00:00`).toLocaleDateString([], { weekday: "short" })}</small></div>)}</div>
      </article>
      <article className="maturity-card"><h2>Card maturity</h2><ul>{["new", "learning", "mature", "locked"].map((state) => <li key={state}>{state}<strong>{data.states[state] ?? 0}</strong></li>)}</ul></article>
    </div>
  </section>;
}
