import { useEffect } from "react";
import { notifications, publishNotification, resolveNotification } from "../lib/notifications";

export function WorkspaceRefreshStatus() {
  useEffect(() => {
    const refreshing = new Set<string>();
    const update = (event: Event) => {
      const detail = (event as CustomEvent<{ state: string; url: string }>).detail;
      if (detail.state === "refreshing") refreshing.add(detail.url);
      else refreshing.delete(detail.url);
      if (refreshing.size) publishNotification({ severity: "info", source: "workspace data",
        key: "workspace-refresh", message: "Showing saved data · refreshing…", active: true });
      else {
        const active = notifications().find((record) => record.key === "workspace-refresh" && !record.resolvedAt);
        if (active) resolveNotification(active.id, { severity: "success", message: "Workspace data refreshed." });
      }
    };
    window.addEventListener("tempo-workspace-data", update);
    return () => window.removeEventListener("tempo-workspace-data", update);
  }, []);
  return null;
}
