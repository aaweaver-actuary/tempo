import { useEffect, useState } from "react";
import { runCoalescedStudyMatch, runStudyTask } from "../lib/background-study";
import type { StudyTask } from "../lib/study-computation";

export function useBackgroundStudy<T>(task: StudyTask | null, empty: T): T {
  const [completed, setCompleted] = useState<{ task: StudyTask; value: T }>();
  useEffect(() => {
    if (!task) return;
    let active = true;
    const controller = new AbortController();
    const work = task.kind === "findPositionMatches"
      ? runCoalescedStudyMatch<T>(task, controller.signal)
      : runStudyTask<T>(task, controller.signal);
    void work.then(value => { if (active) setCompleted({ task, value }); })
      .catch(error => { if (active) console.error("Study diagnostics:", error); });
    return () => { active = false; controller.abort(); };
  }, [task]);
  // Results belong to one input generation; never display stale arrows/diagnostics.
  return completed?.task === task ? completed.value : empty;
}
