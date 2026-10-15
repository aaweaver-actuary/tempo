import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { initializeStudyIntentStorage, legacyStudyCutoverRequired, studyIntentStorageError, studyIntentStorageReady, subscribeStudyIntents } from "../lib/study-intent-storage";
import { Button } from "./buttons/BaseButton";

/** No Study callbacks mount until durable storage has committed its current hydration. */
export function StudyStorageBoundary({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(studyIntentStorageReady);
  const [error, setError] = useState("");
  const [cutover, setCutover] = useState(false);
  const request = useRef(0);
  const mounted = useRef(false);
  const invalidateRequest = useCallback(() => { request.current++; }, []);
  const open = useCallback(async (acknowledge = false) => {
    const generation = ++request.current;
    if (!studyIntentStorageReady()) setReady(false);
    try {
      await initializeStudyIntentStorage(acknowledge);
      if (!studyIntentStorageReady()) throw studyIntentStorageError() ?? new Error("Study storage is unavailable. Retry or reload Tempo.");
      if (mounted.current && request.current === generation) { setReady(true); setError(""); setCutover(false); }
    } catch (failure) {
      if (mounted.current && request.current === generation) { setReady(false); setError(String(failure)); setCutover(legacyStudyCutoverRequired()); }
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    queueMicrotask(() => { if (mounted.current) void open(); });
    const unsubscribe = subscribeStudyIntents(() => {
      if (!studyIntentStorageReady()) {
        invalidateRequest();
        setReady(false); setCutover(legacyStudyCutoverRequired());
        setError(String(studyIntentStorageError() ?? "Close older Tempo tabs and reopen before continuing study."));
      }
    });
    return () => { mounted.current = false; invalidateRequest(); unsubscribe(); };
  }, [open, invalidateRequest]);
  if (ready) return children;
  return <main><p role={error ? "alert" : "status"}>{error || "Loading saved study work…"}</p>
    {error && <><Button onClick={() => void open(cutover)}>{cutover ? "Older tabs are closed — recover study work" : "Retry study storage"}</Button>
      <Button onClick={() => window.location.reload()}>Reload Tempo</Button></>}
  </main>;
}
