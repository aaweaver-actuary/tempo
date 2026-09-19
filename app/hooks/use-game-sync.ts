import { settingsResponseSchema, syncResultSchema, syncStatusSchema } from "../domain/schemas";
import { readJsonResponse } from "../lib/validated-data";
import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "../const";
import { usesLocalApi } from "../utils/local";

type ProviderSyncCounts = {
  provider: "lichess" | "chess.com";
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
      const providerResults = Object.values(result.providers ?? {});
      setState((current) => ({ ...current, syncing: false, lastSuccess: result.synced_at, error: providerResults.filter((provider) => provider.error).map((provider) => `${provider.provider}: ${provider.error}`).join(" · "), imported: result.imported, providers: providerResults }));
    } catch (error) {
      setState((current) => ({ ...current, syncing: false, error: error instanceof Error ? error.message : "Could not sync games." }));
    } finally { active.current = false; }
  }, []);
  useEffect(() => {
    if (!usesLocalApi()) return;
    void fetch(`${API_URL}/api/games/sync/status`).then(async (response) => {
      if (!response.ok) return;
      const result = await readJsonResponse(response, syncStatusSchema, "game sync status");
      const latest = result.providers.map((provider) => provider.last_success_at ?? "").sort().at(-1) ?? "";
      setState((current) => ({ ...current, lastSuccess: current.lastSuccess || latest, error: current.error || result.providers.find((provider) => provider.last_error)?.last_error || "", providers: current.providers?.length ? current.providers : result.providers.flatMap((provider) => provider.last_result ? [provider.last_result] : []), filterLabel: result.active_filters ? `${result.active_filters.rated_only ? "Rated " : ""}${result.active_filters.speeds.join(", ")} · last ${result.active_filters.days} days` : current.filterLabel }));
    }).catch(() => undefined);
    const run = () => void sync();
    const timer = window.setInterval(run, 15_000);
    window.addEventListener("focus", run);
    window.addEventListener("online", run);
    document.addEventListener("visibilitychange", run);
    run();
    return () => { clearInterval(timer); window.removeEventListener("focus", run); window.removeEventListener("online", run); document.removeEventListener("visibilitychange", run); };
  }, [sync]);
  return { state, sync };
}
