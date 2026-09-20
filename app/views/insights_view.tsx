"use client";

import { useState } from "react";
import { useTaskTabs } from "../components/task-tabs";
import ProgressView from "./progress_view";
import StatisticsView from "./statistics_view";

type InsightsTab = "training" | "games";

export default function InsightsView({
  initialTab = "training",
  onTabChange,
  reviewed,
  cardsLeft,
  totalCards,
}: {
  initialTab?: InsightsTab;
  onTabChange?: (tab: InsightsTab) => void;
  reviewed: number;
  cardsLeft: number;
  totalCards: number;
}) {
  const names = ["Training", "Games"] as const;
  const initialName = initialTab === "games" ? "Games" : "Training";
  const [visited, setVisited] = useState<Set<InsightsTab>>(
    () => new Set([initialTab]),
  );
  const tools = useTaskTabs(names, initialName, "tempo-insights-tab", (name) => {
    const nextTab: InsightsTab = name === "Games" ? "games" : "training";
    setVisited((current) => current.has(nextTab) ? current : new Set([...current, nextTab]));
    onTabChange?.(nextTab);
  });
  const activeTab: InsightsTab = tools.activeTab === "Games" ? "games" : "training";

  return (
    <section className="insights-page" {...tools.panelProps}>
      <header className="page-heading compact insights-heading">
        <div>
          <h1>Insights</h1>
          <p>Keep training progress and game performance in one place.</p>
        </div>
      </header>
      <div className="insights-tabs">{tools.tabs}</div>
      <div hidden={!visited.has("training")} aria-hidden={activeTab !== "training"}>
        <ProgressView
          reviewed={reviewed}
          cardsLeft={cardsLeft}
          totalCards={totalCards}
        />
      </div>
      <div hidden={!visited.has("games")} aria-hidden={activeTab !== "games"}>
        <StatisticsView embedded />
      </div>
    </section>
  );
}
