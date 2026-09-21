import { settingsResponseSchema, syncResultSchema, syncStatusSchema } from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";
import type { GameProviderValue, GameSyncJobStatusValue } from "../types";
import { backgroundFetch } from "../lib/background-fetch";
import { reportDebugError } from "../lib/debug-reporting";

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
  const statusFailureCount = useRef(0);
  const recoverStatus = useRef<() => void>(() => undefined);
  const sync = useCallback(async (manual = false, repair = false) => {
    if (!usesLocalApi() || active.current || (!manual && document.visibilityState !== "visible")) return;
    if (!manual && Date.now() - lastStarted.current < interval.current) return;
    active.current = true;
    if (manual) {
      statusFailureCount.current = 0;
      recoverStatus.current();
    }
    let failedEndpoint = `${API_URL}/api/settings`;
    let failedOperation = "load sync settings";
    let failedMethod = "GET";
    try {
      const settingsResponse = await (manual ? fetch : backgroundFetch)(`${API_URL}/api/settings`);
      const settings = await readJsonResponse(settingsResponse, settingsResponseSchema, "game sync settings");
      interval.current = Number(settings.auto_sync_minutes ?? 3) * 60_000;
      if (!settings.lichess_username && !settings.chesscom_username) {
        if (manual) setState((current) => ({ ...current, error: "Add a Lichess or Chess.com username in Settings." }));
        return;
      }
      lastStarted.current = Date.now();
      setState((current) => ({ ...current, syncing: true, error: "" }));
      failedEndpoint = `${API_URL}/api/games/sync`;
      failedOperation = "sync games";
      failedMethod = "POST";
      const response = await (manual ? fetch : backgroundFetch)(`${API_URL}/api/games/sync`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lichess_username: settings.lichess_username, chesscom_username: settings.chesscom_username, days: 90, speeds: ["blitz", "rapid", "classical"], rated_only: true, repair }) });
      const result = await readJsonResponse(response, syncResultSchema, "game sync");
      const providerResults = Object.values(result.providers);
      setState((current) => ({ ...current, syncing: result.status !== "complete" && result.status !== "failed", error: providerResults.filter((provider) => provider.error).map((provider) => `${provider.provider}: ${provider.error}`).join(" · "), imported: result.imported, providers: providerResults, jobStatus: result.status }));
    } catch (error) {
      reportDebugError(error, {
        kind: "api",
        source: "game-sync",
        operation: failedOperation,
        endpoint: failedEndpoint,
        method: failedMethod,
      });
      setState((current) => ({ ...current, syncing: false, error: error instanceof Error ? error.message : "Could not sync games." }));
    } finally { active.current = false; }
  }, []);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let automaticSyncTimer: number | undefined;
    let statusTimer: number | undefined;
    let statusScheduleGeneration = 0;
    const scheduleStatus = (delay: number) => {
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      if (!stopped) statusTimer = window.setTimeout(refreshStatus, delay);
    };
    const refreshStatus = async () => {
      const requestGeneration = statusScheduleGeneration;
      try {
        const response = await backgroundFetch(`${API_URL}/api/games/sync/status`);
        const result = await readJsonResponse(response, syncStatusSchema, "game sync status");
        statusFailureCount.current = 0;
        const latest = result.providers.map((provider) => provider.last_success_at ?? "").sort().at(-1) ?? "";
        const completedResult = result.active_job?.result;
        const providerResults = completedResult
          ? Object.values(completedResult.providers)
          : result.providers.flatMap((provider) => provider.last_result ? [provider.last_result] : []);
        const providerError = result.providers.find((provider) => provider.last_error)?.last_error ?? "";
        const jobStatus = result.active_job?.status;
        const jobIsActive = jobStatus === "queued" || jobStatus === "running" || jobStatus === "paused" || jobStatus === "retrying";
        setState((current) => ({
          ...current,
          syncing: jobStatus ? jobIsActive : active.current ? current.syncing : false,
          jobStatus,
          lastSuccess: completedResult?.synced_at ?? (current.lastSuccess || latest),
          error: (result.active_job?.error ?? providerError) || (completedResult ? "" : current.error),
          imported: completedResult?.imported ?? current.imported,
          providers: providerResults.length ? providerResults : current.providers,
          filterLabel: result.active_filters ? `${result.active_filters.rated_only ? "Rated " : ""}${result.active_filters.speeds.join(", ")} · last ${result.active_filters.days} days` : current.filterLabel,
        }));
        if (requestGeneration === statusScheduleGeneration) {
          scheduleStatus(jobIsActive ? 2_000 : 15_000);
        }
      } catch (error) {
        reportDebugError(error, {
          kind: "api",
          source: "game-sync-status",
          operation: "refresh sync status",
          endpoint: `${API_URL}/api/games/sync/status`,
        });
        const delays = [5_000, 10_000, 20_000, 60_000];
        const failureIndex = Math.min(statusFailureCount.current, delays.length - 1);
        statusFailureCount.current += 1;
        if (requestGeneration === statusScheduleGeneration) {
          scheduleStatus(delays[failureIndex]);
        }
      }
    };
    const scheduleAutomaticSync = (delay = interval.current) => {
      if (automaticSyncTimer !== undefined) window.clearTimeout(automaticSyncTimer);
      if (!stopped) automaticSyncTimer = window.setTimeout(runAutomaticSync, delay);
    };
    const runAutomaticSync = async () => {
      await sync();
      scheduleAutomaticSync();
    };
    const recover = () => {
      statusScheduleGeneration += 1;
      statusFailureCount.current = 0;
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      void refreshStatus();
      void sync();
    };
    recoverStatus.current = recover;
    void refreshStatus();
    void runAutomaticSync();
    window.addEventListener("focus", recover);
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", recover);
    return () => {
      stopped = true;
      recoverStatus.current = () => undefined;
      if (automaticSyncTimer !== undefined) window.clearTimeout(automaticSyncTimer);
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      window.removeEventListener("focus", recover);
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [sync]);
  return { state, sync };
}
