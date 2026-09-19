import { useEffect, useState } from "react";

export function WorkspaceRefreshStatus() {
  const [refreshing, setRefreshing] = useState<Set<string>>(new Set());
  useEffect(() => {
    const update = (event: Event) => {
      const detail = (event as CustomEvent<{ state: string; url: string }>).detail;
      setRefreshing((current) => {
        const next = new Set(current);
        if (detail.state === "refreshing") next.add(detail.url);
        else next.delete(detail.url);
        return next;
      });
    };
    window.addEventListener("tempo-workspace-data", update);
    return () => window.removeEventListener("tempo-workspace-data", update);
  }, []);
  if (!refreshing.size) return null;
  return <div className="workspace-refresh-status" role="status">Showing saved data · refreshing…</div>;
}
