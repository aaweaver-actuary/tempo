"use client";
import { Notice } from "../components/task-tabs";
import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { readWorkspaceResponse } from "../lib/workspace-data";
import { usesLocalApi } from "../utils/local";
import { reportDebugError } from "../lib/debug-reporting";

type Progress = {
  states: Record<string, number>;
  activity: { date: string; count: number }[];
  reviewedToday: number;
  cleanCards: number;
  dueToday: number;
  blockedDue?: number;
  totalCards: number;
};

export default function ProgressView({
  reviewed,
  cardsLeft,
  totalCards,
}: {
  reviewed: number;
  cardsLeft: number;
  totalCards: number;
}) {
  const [data, setData] = useState<Progress>({
    states: {},
    activity: [],
    reviewedToday: reviewed,
    cleanCards: 0,
    dueToday: cardsLeft,
    totalCards,
  });
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(!usesLocalApi());
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let active = true;
    void readWorkspaceResponse(`${API_URL}/api/progress`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const summary = (await response.json()) as Progress;
        if (!summary || !Array.isArray(summary.activity) || typeof summary.totalCards !== "number" || !summary.states) throw new Error("Malformed progress response");
        if (active) { setData(summary); setLoaded(true); setError(""); }
      })
      .catch((failure) => {
        reportDebugError(failure, {
          kind: "api",
          source: "progress-view",
          operation: "load review history",
          endpoint: `${API_URL}/api/progress`,
        });
        if (active)
          setError(`Review history unavailable: ${failure instanceof Error ? failure.message : "connection failed"}. Check the local service and retry.`);
      });
    return () => {
      active = false;
    };
  }, [retry]);
  const maximum = Math.max(1, ...data.activity.map((day) => day.count));
  return (
    <section className="progress-page" id="progress">
      <div className="page-heading compact">
        <h1 className="sr-only">Progress</h1>
      </div>
      {error && <Notice error onRetry={() => setRetry(value => value + 1)}>{error}</Notice>}
      {!loaded && !error && <Notice>Loading progress…</Notice>}
      {loaded && <>
      <div className="metric-grid">
        {[
          ["Due today", data.dueToday],
          ...(data.blockedDue ? [["Paused for repair", data.blockedDue] as [string, number]] : []),
          ["Cards available", data.totalCards],
          ["Reviewed today", data.reviewedToday],
          ["Cards recalled cleanly", data.cleanCards],
        ].map(([label, count]) => (
          <article key={label}>
            <span>{label}</span>
            <strong>{count}</strong>
          </article>
        ))}
      </div>
      <div className="analytics-grid">
        <article className="chart-card">
          <div className="chart-heading">
            <strong>Review activity</strong>
            <small>Last 7 days</small>
          </div>
          <div className="bar-chart">
            {data.activity.map((day) => (
              <div className="bar-column" key={day.date}>
                <b>{day.count}</b>
                <span style={{ height: `${(day.count / maximum) * 130}px` }} />
                <small>
                  {new Date(`${day.date}T12:00:00`).toLocaleDateString([], {
                    weekday: "short",
                  })}
                </small>
              </div>
            ))}
          </div>
        </article>
        <article className="maturity-card">
          <h2>Card maturity</h2>
          <ul>
            {["new", "learning", "mature", "locked"].map((state) => (
              <li key={state}>
                {state}
                <strong>{data.states[state] ?? 0}</strong>
              </li>
            ))}
          </ul>
        </article>
      </div>
      </>}
    </section>
  );
}
