import { useSyncExternalStore } from "react";
import {
  dataDiagnostics,
  subscribeDataDiagnostics,
} from "../lib/validated-data";

export function DataDiagnosticsNotice() {
  const diagnostics = useSyncExternalStore(
    subscribeDataDiagnostics,
    dataDiagnostics,
    dataDiagnostics,
  );
  if (!diagnostics.length) return null;
  const exportDiagnostics = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(diagnostics, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "tempo-data-diagnostics.json";
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <details className="data-diagnostics-notice">
      <summary>
        {diagnostics.length} data issue{diagnostics.length === 1 ? "" : "s"} set
        aside for repair
      </summary>
      <ul>
        {diagnostics.map((issue, index) => (
          <li key={index}>
            {issue.source}
            {issue.recordId ? ` · ${issue.recordId}` : ""}: {issue.message}
          </li>
        ))}
      </ul>
      <button onClick={exportDiagnostics}>Export diagnostics for repair</button>
    </details>
  );
}
