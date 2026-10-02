import { settingsResponseSchema, syncResultSchema, syncStatusSchema } from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";
import type { GameProviderValue, GameSyncJobStatusValue } from "../types";
import { backgroundFetch } from "../lib/background-fetch";
import { reportDebugError, resolveApiIncidentsForEndpoint, resolveValidationIncidentsForEndpoint } from "../lib/debug-reporting";
import { enqueueGameSyncCommand, hasPendingGameSyncCommand } from "../lib/game-sync-command";
import { PendingOperationError } from "../lib/operation-status";

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
type SyncErrorOwner = "none" | "status" | "command";

function sameSyncState(left: GameSyncState, right: GameSyncState) {
  const leftProviders = left.providers ?? [];
  const rightProviders = right.providers ?? [];
  return left.syncing === right.syncing && left.lastSuccess === right.lastSuccess && left.error === right.error
    && left.imported === right.imported && left.filterLabel === right.filterLabel && left.jobStatus === right.jobStatus
    && leftProviders.length === rightProviders.length && leftProviders.every((provider, index) => {
      const other = rightProviders[index];
      return provider.provider === other.provider && provider.fetched === other.fetched && provider.inserted === other.inserted
        && provider.updated === other.updated && provider.duplicates === other.duplicates && provider.filtered === other.filtered
        && provider.rejected === other.rejected && provider.failed === other.failed;
    });
}

export function useGameSync() {
  const [state, setState] = useState<GameSyncState>({ syncing: false, lastSuccess: "", error: "", imported: 0, providers: [], filterLabel: "Rated blitz, rapid, and classical · last 90 days" });
  // Track queued state and error ownership together, including updates before React commits.
  const requestedState = useRef({ state, errorOwner: "none" as SyncErrorOwner });
  const publishState = useCallback((project: (current: GameSyncState) => GameSyncState,
    errorUpdate?: { message: string; owner: Exclude<SyncErrorOwner, "none"> }) => {
    const previous = requestedState.current;
    const next = { ...project(previous.state), error: errorUpdate?.message ?? previous.state.error };
    const errorOwner = errorUpdate ? errorUpdate.message ? errorUpdate.owner : "none" : previous.errorOwner;
    const nextState = sameSyncState(previous.state, next) ? previous.state : next;
    requestedState.current = { state: nextState, errorOwner };
    if (nextState !== previous.state) setState(nextState);
  }, []);
  const active = useRef(false);
  const interval = useRef(180_000);
  const lastStarted = useRef(0);
  const statusFailureCount = useRef(0);
  const lastReportedStatusFailure = useRef("");
  const manualCommandPending = useRef(false);
  const invalidateStatusForCommand = useRef<() => void>(() => undefined);
  const requestStatusRecovery = useRef<() => void>(() => undefined);
  const resumeStatusPolling = useRef<() => void>(() => undefined);
  const sync = useCallback(async (manual = false, repair = false) => {
    if (!usesLocalApi() || active.current || (!manual && document.visibilityState !== "visible")) return;
    if (!manual && Date.now() - lastStarted.current < interval.current) return;
    active.current = true;
    if (manual) {
      manualCommandPending.current = true;
      statusFailureCount.current = 0;
      invalidateStatusForCommand.current();
    }
    let failedEndpoint = `${API_URL}/api/settings`;
    let failedOperation = "load sync settings";
    let failedMethod = "GET";
    let commandNeedsStatusReconciliation = false;
    try {
      const settingsResponse = await (manual ? fetch : backgroundFetch)(`${API_URL}/api/settings`);
      const settings = await readJsonResponse(settingsResponse, settingsResponseSchema, "game sync settings", { endpoint: failedEndpoint, reportHttpFailure: false });
      interval.current = Number(settings.auto_sync_minutes ?? 3) * 60_000;
      if (!settings.lichess_username && !settings.chesscom_username) {
        if (manual) publishState(current => current,
          { message: "Add a Lichess or Chess.com username in Settings.", owner: "command" });
        return;
      }
      lastStarted.current = Date.now();
      // Starting another attempt alone cannot clear an earlier actionable command failure.
      publishState(current => ({ ...current, syncing: true }));
      failedEndpoint = `${API_URL}/api/games/sync`;
      failedOperation = "sync games";
      failedMethod = "POST";
      const response = await enqueueGameSyncCommand({ lichess_username: settings.lichess_username, chesscom_username: settings.chesscom_username, days: 90, speeds: ["blitz", "rapid", "classical"], rated_only: true, repair }, manual ? fetch : backgroundFetch);
      const result = await readJsonResponse(response, syncResultSchema, "game sync", { endpoint: failedEndpoint, reportHttpFailure: false });
      commandNeedsStatusReconciliation = true;
      const providerResults = Object.values(result.providers);
      // A validated command result releases local-error ownership to server status/reconciliation.
      publishState(current => ({ ...current, syncing: result.status !== "complete" && result.status !== "failed",
        imported: result.imported, providers: providerResults, jobStatus: result.status }),
        { message: providerResults.filter(provider => provider.error).map(provider => `${provider.provider}: ${provider.error}`).join(" · "), owner: "status" });
    } catch (error) {
      if (error instanceof PendingOperationError) {
        // Unresolved receipts keep durable command recovery; blocked receipts retain their actionable error.
        commandNeedsStatusReconciliation = !error.blocked;
        publishState(current => ({ ...current, syncing: !error.blocked }),
          { message: error.blocked ? error.message : "", owner: "command" });
        return;
      }
      reportDebugError(error, {
        kind: "api",
        source: "game-sync",
        operation: failedOperation,
        endpoint: failedEndpoint,
        method: failedMethod,
      });
      publishState(current => ({ ...current, syncing: false }),
        { message: error instanceof Error ? error.message : "Could not sync games.", owner: "command" });
    } finally {
      active.current = false;
      if (manual) {
        manualCommandPending.current = false;
        if (commandNeedsStatusReconciliation) requestStatusRecovery.current();
        else resumeStatusPolling.current();
      }
    }
  }, [publishState]);
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let automaticSyncTimer: number | undefined;
    let statusTimer: number | undefined;
    let statusInFlight = false;
    let statusGeneration = 0;
    let statusRefreshPending = false;
    let statusWakeQueued = false;
    let nextStatusDelay = 15_000;
    const statusEligible = () => !stopped && !manualCommandPending.current
      && document.visibilityState === "visible" && navigator.onLine;
    const scheduleStatus = (delay: number) => {
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      if (statusEligible()) statusTimer = window.setTimeout(refreshStatus, delay);
    };
    const refreshStatus = async () => {
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      if (!statusEligible()) return;
      if (statusInFlight) { statusRefreshPending = true; return; }
      statusInFlight = true;
      do {
        statusRefreshPending = false;
        const requestGeneration = statusGeneration;
        try {
          const response = await backgroundFetch(`${API_URL}/api/games/sync/status`);
          if (stopped) break;
          if (requestGeneration !== statusGeneration) continue;
          const result = await readJsonResponse(response, syncStatusSchema, "game sync status", { endpoint: `${API_URL}/api/games/sync/status`, reportHttpFailure: false });
          if (stopped) break;
          if (requestGeneration !== statusGeneration) continue;
          resolveValidationIncidentsForEndpoint(`${API_URL}/api/games/sync/status`);
          resolveApiIncidentsForEndpoint(`${API_URL}/api/games/sync/status`, "game-sync-status");
          statusFailureCount.current = 0;
          lastReportedStatusFailure.current = "";
          const latest = result.providers.map((provider) => provider.last_success_at ?? "").sort().at(-1) ?? "";
          const completedResult = result.active_job?.result;
          const providerResults = completedResult
            ? Object.values(completedResult.providers)
            : result.providers.flatMap((provider) => provider.last_result ? [provider.last_result] : []);
          const providerError = result.providers.find((provider) => provider.last_error)?.last_error ?? "";
          const jobStatus = result.active_job?.status;
          const jobIsActive = jobStatus === "queued" || jobStatus === "running" || jobStatus === "paused" || jobStatus === "retrying";
          const statusError = (result.active_job?.error ?? providerError) || "";
          // Passive jobs, historical or active, cannot replace a newer local command error.
          // A subsequent validated result or non-blocked pending receipt above releases that ownership.
          const errorUpdate = requestedState.current.errorOwner !== "command" && (statusError || completedResult)
            ? { message: statusError, owner: "status" as const } : undefined;
          const projectState = (current: GameSyncState): GameSyncState => ({
            ...current,
            syncing: jobStatus ? jobIsActive : active.current ? current.syncing : hasPendingGameSyncCommand(),
            jobStatus,
            lastSuccess: completedResult?.synced_at ?? (current.lastSuccess || latest),
            imported: completedResult?.imported ?? current.imported,
            providers: providerResults.length ? providerResults.map(provider => ({
              provider: provider.provider, fetched: provider.fetched, inserted: provider.inserted, updated: provider.updated,
              duplicates: provider.duplicates, filtered: provider.filtered, rejected: provider.rejected, failed: provider.failed,
            })).sort((left, right) => left.provider.localeCompare(right.provider)) : current.providers,
            filterLabel: result.active_filters ? `${result.active_filters.rated_only ? "Rated " : ""}${result.active_filters.speeds.join(", ")} · last ${result.active_filters.days} days` : current.filterLabel,
          });
          publishState(projectState, errorUpdate);
          nextStatusDelay = jobIsActive ? 2_000 : 15_000;
        } catch (error) {
          if (stopped) break;
          if (requestGeneration !== statusGeneration) continue;
          const failureSignature = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
          if (failureSignature !== lastReportedStatusFailure.current) {
            reportDebugError(error, {
              kind: "api",
              source: "game-sync-status",
              operation: "refresh sync status",
              endpoint: `${API_URL}/api/games/sync/status`,
            });
            lastReportedStatusFailure.current = failureSignature;
          }
          const delays = [5_000, 10_000, 20_000, 60_000];
          const failureIndex = Math.min(statusFailureCount.current, delays.length - 1);
          statusFailureCount.current += 1;
          nextStatusDelay = delays[failureIndex];
        }
      } while (statusRefreshPending && statusEligible());
      statusInFlight = false;
      scheduleStatus(nextStatusDelay);
    };
    const scheduleAutomaticSync = (delay = interval.current) => {
      if (automaticSyncTimer !== undefined) window.clearTimeout(automaticSyncTimer);
      if (!stopped) automaticSyncTimer = window.setTimeout(runAutomaticSync, delay);
    };
    const runAutomaticSync = async () => {
      await sync();
      scheduleAutomaticSync();
    };
    const recoverStatus = () => {
      statusFailureCount.current = 0;
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      if (statusEligible()) {
        if (!statusWakeQueued) {
          statusWakeQueued = true;
          queueMicrotask(() => { statusWakeQueued = false; if (statusEligible()) void refreshStatus(); });
        }
      } else statusRefreshPending = false;
    };
    const recover = () => {
      recoverStatus();
      // Acquisition/recovery retains its existing visibility and cadence rules.
      void sync();
    };
    const pauseStatus = () => {
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      statusRefreshPending = false;
    };
    invalidateStatusForCommand.current = () => {
      // Fence existing reads and suspend timers until the command establishes its state.
      statusGeneration += 1;
      pauseStatus();
    };
    requestStatusRecovery.current = recoverStatus;
    resumeStatusPolling.current = () => {
      // Resuming after a local command failure does not reconcile against an unrelated historical job.
      if (!statusInFlight) scheduleStatus(nextStatusDelay);
    };
    void refreshStatus();
    void runAutomaticSync();
    window.addEventListener("focus", recover);
    window.addEventListener("online", recover);
    window.addEventListener("offline", pauseStatus);
    document.addEventListener("visibilitychange", recover);
    return () => {
      stopped = true;
      invalidateStatusForCommand.current = () => undefined;
      requestStatusRecovery.current = () => undefined;
      resumeStatusPolling.current = () => undefined;
      if (automaticSyncTimer !== undefined) window.clearTimeout(automaticSyncTimer);
      if (statusTimer !== undefined) window.clearTimeout(statusTimer);
      window.removeEventListener("focus", recover);
      window.removeEventListener("online", recover);
      window.removeEventListener("offline", pauseStatus);
      document.removeEventListener("visibilitychange", recover);
    };
  }, [sync, publishState]);
  return { state, sync };
}
