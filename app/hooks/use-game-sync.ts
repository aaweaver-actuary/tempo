import { settingsResponseSchema, syncResultSchema, syncStatusSchema } from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";
import type { GameProviderValue, GameSyncJobStatusValue } from "../types";

type ProviderSyncCounts = {
  provider: GameProviderValue;
  fetched: number;
  inserted: number;
  updated: number;
  duplicates: number;
  filtered: number;
  rejected: number;
  failed: number;
};
export type GameSyncState = {
  syncing: boolean;
  lastSuccess: string;
  error: string;
  imported: number;
  providers?: ProviderSyncCounts[];
  filterLabel?: string;
  jobStatus?: GameSyncJobStatusValue;
};

export function useGameSync() {
  const [state, setState] = useState<GameSyncState>({ syncing: false, lastSuccess: "", error: "", imported: 0, providers: [], filterLabel: "Rated blitz, rapid, and classical · last 90 days" });
  const active = useRef(false);
  const interval = useRef(180_000);
  const lastStarted = useRef(0);
  const sync = useCallback(async (manual = false, repair = false) => {
    if (!usesLocalApi() || active.current || (!manual && document.visibilityState !== "visible")) return;
    if (!manual && Date.now() - lastStarted.current < interval.current) return;
    active.current = true;
    try {
      const settingsResponse = await fetch(`${API_URL}/api/settings`);
      if (!settingsResponse.ok) throw new Error("Could not reach the local service.");
      const settings = await readJsonResponse(settingsResponse, settingsResponseSchema, "game sync settings");
      interval.current = Number(settings.auto_sync_minutes ?? 3) * 60_000;
      if (!settings.lichess_username && !settings.chesscom_username) {
        if (manual) setState((current) => ({ ...current, error: "Add a Lichess or Chess.com username in Settings." }));
        return;
      }
      lastStarted.current = Date.now();
      setState((current) => ({ ...current, syncing: true, error: "" }));
      const response = await fetch(`${API_URL}/api/games/sync`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lichess_username: settings.lichess_username, chesscom_username: settings.chesscom_username, days: 90, speeds: ["blitz", "rapid", "classical"], rated_only: true, repair }) });
      const result = await readJsonResponse(response, syncResultSchema, "game sync");
      const providerResults = Object.values(result.providers);
      setState((current) => ({ ...current, syncing: result.status !== "complete" && result.status !== "failed", error: providerResults.filter((provider) => provider.error).map((provider) => `${provider.provider}: ${provider.error}`).join(" · "), imported: result.imported, providers: providerResults, jobStatus: result.status }));
    } catch (error) {
      setState((current) => ({ ...current, syncing: false, error: error instanceof Error ? error.message : "Could not sync games." }));
    } finally { active.current = false; }
  }, []);
  useEffect(() => {
    if (!usesLocalApi()) return;
    const refreshStatus = () => void fetch(`${API_URL}/api/games/sync/status`).then(async (response) => {
      if (!response.ok) return;
      const result = await readJsonResponse(response, syncStatusSchema, "game sync status");
      const latest = result.providers.map((provider) => provider.last_success_at ?? "").sort().at(-1) ?? "";
      const completedResult = result.active_job?.result;
      const providerResults = completedResult
        ? Object.values(completedResult.providers)
        : result.providers.flatMap((provider) => provider.last_result ? [provider.last_result] : []);
      const providerError = result.providers.find((provider) => provider.last_error)?.last_error ?? "";
      const jobStatus = result.active_job?.status;
      setState((current) => ({
        ...current,
        syncing: jobStatus
          ? jobStatus === "queued" || jobStatus === "running" || jobStatus === "paused" || jobStatus === "retrying"
          : active.current
            ? current.syncing
            : false,
        jobStatus,
        lastSuccess: completedResult?.synced_at ?? (current.lastSuccess || latest),
        error:
          (result.active_job?.error ?? providerError) ||
          (completedResult ? "" : current.error),
        imported: completedResult?.imported ?? current.imported,
        providers: providerResults.length ? providerResults : current.providers,
        filterLabel: result.active_filters ? `${result.active_filters.rated_only ? "Rated " : ""}${result.active_filters.speeds.join(", ")} · last ${result.active_filters.days} days` : current.filterLabel,
      }));
    }).catch(() => undefined);
    refreshStatus();
    const run = () => void sync();
    const timer = window.setInterval(run, 15_000);
    const statusTimer = window.setInterval(refreshStatus, 2_000);
    window.addEventListener("focus", run);
    window.addEventListener("online", run);
    document.addEventListener("visibilitychange", run);
    run();
    return () => { clearInterval(timer); clearInterval(statusTimer); window.removeEventListener("focus", run); window.removeEventListener("online", run); document.removeEventListener("visibilitychange", run); };
  }, [sync]);
  return { state, sync };
}
