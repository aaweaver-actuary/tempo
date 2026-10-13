import { reportDebugError } from "./debug-reporting";

export const confirmedStudyControlsChangeKey = "tempo-confirmed-study-controls-change-v1";

// A notification invalidates prior reads; only a fresh GET can authorize work.
// Never call this for pending or failed settings saves.
export function announceConfirmedStudyControlsChange() {
  try {
    localStorage.setItem(confirmedStudyControlsChangeKey, crypto.randomUUID());
  } catch (error) {
    // The database save is already confirmed. Preserve that outcome and report
    // the unavailable cross-tab notification rather than reporting a failed save.
    reportDebugError(error, { kind: "uncaught-exception", source: "study-controls-notification" });
  }
  window.dispatchEvent(new Event("tempo:study-mode-changed"));
}

export function confirmedStudyControlsChangeToken() {
  try { return localStorage.getItem(confirmedStudyControlsChangeKey); }
  catch { return null; }
}
