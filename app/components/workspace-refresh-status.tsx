import { useEffect } from "react";
import { hydrateNotifications, notifications, publishNotification, resolveNotification, sanitizeNotificationText, updateNotification } from "../lib/notifications";

export function WorkspaceRefreshStatus() {
  useEffect(() => {
    const refreshing = new Set<string>();
    hydrateNotifications();
    const previousFailure = notifications().find((record) => record.key === "workspace-refresh" && !record.resolvedAt);
    const savedFailedUrls = previousFailure?.details?.failedUrls;
    const failedRefreshes = new Set<string>(Array.isArray(savedFailedUrls)
      ? savedFailedUrls.filter((url): url is string => typeof url === "string") : []);
    const update = (event: Event) => {
      const detail = (event as CustomEvent<{ state: string; url: string }>).detail;
      const refreshUrl = sanitizeNotificationText(detail.url);
      if (detail.state === "refreshing") refreshing.add(refreshUrl);
      else {
        if (detail.state === "error" && refreshing.has(refreshUrl)) failedRefreshes.add(refreshUrl);
        if (detail.state === "ready") failedRefreshes.delete(refreshUrl);
        refreshing.delete(refreshUrl);
      }
      const active = notifications().find((record) => record.key === "workspace-refresh" && !record.resolvedAt);
      if (failedRefreshes.size) {
        if (active) updateNotification(active.id, { severity: "warning", active: refreshing.size > 0,
          message: "Could not refresh workspace data. Showing saved data; retry when the service is available.",
          details: { failedUrls: Array.from(failedRefreshes) } });
      } else if (refreshing.size) publishNotification({ severity: "info", source: "workspace data",
        key: "workspace-refresh", message: "Showing saved data · refreshing…", active: true });
      else if (detail.state === "ready") {
        if (active) resolveNotification(active.id, { severity: "success", message: "Workspace data refreshed." });
      }
    };
    window.addEventListener("tempo-workspace-data", update);
    return () => window.removeEventListener("tempo-workspace-data", update);
  }, []);
  return null;
}
