import { useEffect, useState } from "react";
import { runStudyTask } from "../lib/background-study";
import type { AnalysisLine } from "../types";

let nextIndexRevision = 0;

type ReadyPositionIndex = { repertoireId: string; revision: number };

export function useStudyPositionIndex(
  repertoireId: string | undefined,
  lines: AnalysisLine[],
): ReadyPositionIndex | undefined {
  const [completed, setCompleted] = useState<{
    repertoireId: string;
    lines: AnalysisLine[];
    revision: number;
  }>();
  useEffect(() => {
    if (!repertoireId || lines.length === 0) return;
    const revision = ++nextIndexRevision;
    const controller = new AbortController();
    let active = true;
    void runStudyTask({
      kind: "initializePositionIndex", repertoireId, revision, lines,
    }, controller.signal).then(() => {
      if (active) setCompleted({ repertoireId, lines, revision });
    }).catch((error) => {
      if (active) console.error("Study position index:", error);
    });
    return () => {
      active = false;
      controller.abort();
      void runStudyTask({ kind: "releasePositionIndex", repertoireId, revision })
        .catch(() => undefined);
    };
  }, [repertoireId, lines]);
  if (!completed || !repertoireId || completed.repertoireId !== repertoireId || completed.lines !== lines)
    return undefined;
  return completed;
}
