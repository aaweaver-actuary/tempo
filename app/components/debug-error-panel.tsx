import { useState, useSyncExternalStore } from "react";
import {
  buildDebugBundle,
  copyDebugBundle,
  debugErrors,
  subscribeDebugErrors,
  type DebugErrorRecord,
} from "../lib/debug-reporting";

export function DebugErrorPanel({ boundaryFallback = false }: { boundaryFallback?: boolean }) {
  const errors = useSyncExternalStore(
    subscribeDebugErrors,
    debugErrors,
    debugErrors,
  );
  const latest = errors[errors.length - 1];
  const [dismissedId, setDismissedId] = useState<string>();
  const [copyRecordId, setCopyRecordId] = useState<string>();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");

  if (!latest && !boundaryFallback) return null;
  if (!latest || latest.id === dismissedId) {
    return boundaryFallback ? (
      <section className="tempo-debug-panel tempo-debug-panel-boundary" role="alert">
        <strong>Tempo encountered an unexpected error.</strong>
        <p>Reload the page and try again. If it repeats, use the debug information after the error is captured.</p>
      </section>
    ) : null;
  }

  const payload = buildDebugBundle(latest.id);
  return (
    <section
      className={`tempo-debug-panel${boundaryFallback ? " tempo-debug-panel-boundary" : ""}`}
      role={boundaryFallback ? "alert" : "region"}
      aria-label={boundaryFallback ? undefined : "Tempo error details"}
    >
      <div className="tempo-debug-heading">
        <strong>Tempo encountered an error</strong>
        <span>{latest.context.source} · {latest.kind}</span>
      </div>
      <p>{latest.message}</p>
      <div className="tempo-debug-actions">
        <button
          type="button"
          onClick={() => {
            setCopyRecordId(latest.id);
            void copyDebugBundle(latest.id).then((copied) => {
              setCopyState(copied ? "copied" : "failed");
            });
          }}
        >
          {copyRecordId === latest.id && copyState === "copied" ? "Copied debug info" : "Copy debug info"}
        </button>
        <button type="button" onClick={() => setDismissedId(latest.id)}>Dismiss</button>
      </div>
      {copyRecordId === latest.id && copyState === "failed" && (
        <p className="tempo-debug-copy-help">Clipboard access was unavailable. Select the debug information below and copy it manually.</p>
      )}
      <details>
        <summary>Preview debug information</summary>
        <textarea aria-label="Debug information" readOnly value={payload} />
      </details>
    </section>
  );
}

export function latestDebugError(): DebugErrorRecord | undefined {
  const errors = debugErrors();
  return errors[errors.length - 1];
}
