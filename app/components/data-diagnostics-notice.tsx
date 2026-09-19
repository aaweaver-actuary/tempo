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
  const groupedDiagnostics = Array.from(
    diagnostics.reduce((groups, issue) => {
      const key = `${issue.source}\u0000${issue.message}`;
      const group = groups.get(key) ?? { issue, count: 0, recordIds: [] as string[] };
      group.count += 1;
      if (issue.recordId && group.recordIds.length < 3)
        group.recordIds.push(issue.recordId);
      groups.set(key, group);
      return groups;
    }, new Map<string, { issue: (typeof diagnostics)[number]; count: number; recordIds: string[] }>()),
  ).map(([, group]) => group);
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
        {groupedDiagnostics.length} data issue type
        {groupedDiagnostics.length === 1 ? "" : "s"} affecting {diagnostics.length}{" "}
        record{diagnostics.length === 1 ? "" : "s"}
      </summary>
      <ul>
        {groupedDiagnostics.map(({ issue, count, recordIds }) => (
          <li key={`${issue.source}:${issue.message}`}>
            {issue.source} · {count} affected
            {recordIds.length ? ` · examples ${recordIds.join(", ")}` : ""}: {issue.message}
          </li>
        ))}
      </ul>
      <button onClick={exportDiagnostics}>Export diagnostics for repair</button>
    </details>
  );
}
