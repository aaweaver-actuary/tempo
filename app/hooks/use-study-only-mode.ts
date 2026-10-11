import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";

// Unknown mode suppresses optional preparation until authoritative settings arrive.
export function useStudyOnlyMode() {
  const [studyOnlyMode, setStudyOnlyMode] = useState<boolean | null>(() => usesLocalApi() ? null : false);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let reading = false;
    const refresh = async () => {
      if (reading || document.visibilityState !== "visible") return;
      reading = true;
      try {
        const response = await fetch(`${API_URL}/api/settings`, { signal: AbortSignal.timeout(5_000) });
        if (!response.ok) throw new Error("Study mode settings unavailable");
        const settings = await response.json() as { study_only_mode?: unknown };
        if (settings.study_only_mode !== undefined && typeof settings.study_only_mode !== "boolean")
          throw new Error("Study mode settings are invalid");
        if (!stopped) setStudyOnlyMode(settings.study_only_mode === true);
      } catch {
        // Retain the last authoritative mode; never authorize optional work on failure.
      } finally { reading = false; }
    };
    void refresh();
    const wake = () => void refresh();
    const timer = window.setInterval(wake, 30_000);
    window.addEventListener("tempo:study-mode-changed", wake);
    document.addEventListener("visibilitychange", wake);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      window.removeEventListener("tempo:study-mode-changed", wake);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);
  return studyOnlyMode;
}
