import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";

// Unknown mode suppresses optional preparation until authoritative settings arrive.
type StudyControls = { studyOnlyMode: boolean; repertoirePreparationEnabled: boolean; optionalAnalysisAndImportsEnabled: boolean };

export function useStudyControls() {
  const [controls, setControls] = useState<StudyControls | null>(() => usesLocalApi() ? null : { studyOnlyMode: false, repertoirePreparationEnabled: true, optionalAnalysisAndImportsEnabled: true });
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let reading = false;
    let refreshAgain = false;
    const refresh = async () => {
      if (document.visibilityState !== "visible") return;
      if (reading) { refreshAgain = true; return; }
      reading = true;
      refreshAgain = false;
      try {
        const response = await fetch(`${API_URL}/api/settings`, { signal: AbortSignal.timeout(5_000) });
        if (!response.ok) throw new Error("Study mode settings unavailable");
        const settings = await response.json() as Record<string, unknown>;
        if (typeof settings.study_only_mode !== "boolean") throw new Error("Study mode settings are unknown");
        if (["study_only_mode", "repertoire_preparation_enabled", "optional_analysis_and_imports_enabled"].some(field => settings[field] !== undefined && typeof settings[field] !== "boolean"))
          throw new Error("Study mode settings are invalid");
        if (!stopped && !refreshAgain) setControls({ studyOnlyMode: settings.study_only_mode === true,
          repertoirePreparationEnabled: settings.repertoire_preparation_enabled !== false,
          optionalAnalysisAndImportsEnabled: settings.optional_analysis_and_imports_enabled !== false });
      } catch {
        // Retain the last authoritative mode; never authorize optional work on failure.
      } finally {
        reading = false;
        if (!stopped && refreshAgain) void refresh();
      }
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
  return controls;
}

export function useStudyOnlyMode() {
  return useStudyControls()?.studyOnlyMode ?? null;
}
