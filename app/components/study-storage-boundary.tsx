import { useEffect, useState, type ReactNode } from "react";
import { initializeStudyIntentStorage, legacyStudyCutoverRequired, studyIntentStorageReady, subscribeStudyIntents } from "../lib/study-intent-storage";
import { Button } from "./buttons/BaseButton";

/** No Study callbacks mount until durable storage has committed its initial hydration. */
export function StudyStorageBoundary({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(studyIntentStorageReady);
  const [error, setError] = useState("");
  const [cutover, setCutover] = useState(false);
  const open = async (acknowledge = false) => {
    try { await initializeStudyIntentStorage(acknowledge); setReady(true); setError(""); }
    catch (failure) { setReady(false); setError(String(failure)); setCutover(legacyStudyCutoverRequired()); }
  };
  useEffect(() => {
    queueMicrotask(() => void open());
    return subscribeStudyIntents(() => { if (!studyIntentStorageReady()) { setReady(false); setCutover(legacyStudyCutoverRequired()); setError("Close older Tempo tabs and reopen before continuing study."); } });
  }, []);
  if (ready) return children;
  return <main><p role={error ? "alert" : "status"}>{error || "Loading saved study work…"}</p>
    {error && <Button onClick={() => void open(cutover)}>{cutover ? "Older tabs are closed — recover study work" : "Retry study storage"}</Button>}
  </main>;
}
