import { useEffect, useRef, useState } from "react";
import type { GameRefreshStatus, GameSummary } from "../product-api/contracts";
import { GameReviewRequestError, type GameReviewClient } from "./api";
import { FailedOperationError, PendingOperationError } from "../../lib/operation-status";
import { readPendingGameRefresh, storePendingGameRefresh, clearPendingGameRefresh } from "../../../database/browser/adapters/games-analysis/game-sync";

type Provider = GameSummary["provider"];
type Pending = { provider: Provider; operation_id: string; job_id?: string };
type Progress = { job?: GameRefreshStatus; pending: boolean; error?: string };
const providers = ["lichess", "chesscom"] as const;

export function useGameRefresh(api: GameReviewClient, onComplete: () => void) {
  const [progress, setProgress] = useState<Record<Provider, Progress>>({ lichess: { pending: false }, chesscom: { pending: false } });
  const completed = useRef(onComplete);
  useEffect(() => { completed.current = onComplete; }, [onComplete]);
  const trigger = useRef<(provider: Provider) => void>(() => undefined);

  useEffect(() => {
    let stopped = false;
    const scope = api.refreshScope ?? `${window.location.origin}:legacy:game-refresh`;
    const timers = new Map<Provider, ReturnType<typeof setTimeout>>();
    const requests = new Map<Provider, AbortController>();
    const read = (provider: Provider): Pending | null => {
      const raw = readPendingGameRefresh(scope, provider);
      if (!raw) return null;
      const value = JSON.parse(raw) as Pending;
      if (value.provider !== provider || typeof value.operation_id !== "string" || !value.operation_id
          || value.job_id !== undefined && typeof value.job_id !== "string")
        throw new Error("The original refresh request could not be recovered. Its saved identity must be resolved before starting another.");
      return value;
    };
    const publish = (provider: Provider, value: Progress) => {
      if (!stopped) setProgress(previous => ({ ...previous, [provider]: value }));
    };
    const run = async (provider: Provider, create = false) => {
      if (stopped || requests.has(provider) || document.visibilityState === "hidden") return;
      clearTimeout(timers.get(provider));
      const controller = new AbortController();
      requests.set(provider, controller);
      let pending: Pending | null = null;
      try {
        pending = read(provider);
        if (!pending && !create) return;
        if (!pending) {
          const original = { provider, operation_id: crypto.randomUUID() };
          storePendingGameRefresh(scope, provider, original);
          pending = original;
        }
        if (!stopped) setProgress(previous => ({ ...previous, [provider]: { ...(pending?.job_id ? previous[provider] : {}), pending: true, error: undefined } }));
        const status = pending.job_id
          ? await api.refreshStatus({ operation_id: pending.operation_id, signal: controller.signal })
          : await api.refresh({ provider, operation_id: pending.operation_id, signal: controller.signal });
        if (stopped || controller.signal.aborted) return;
        if (status.operation_id !== pending.operation_id || status.provider !== provider
            || pending.job_id && status.job_id !== pending.job_id)
          throw new Error("Refresh status does not match the original request.");
        const terminal = ["complete", "failed", "quarantined"].includes(status.status);
        const current = read(provider);
        const ownsRecord = current?.operation_id === pending.operation_id;
        if (ownsRecord && terminal) clearPendingGameRefresh(scope, provider);
        else if (ownsRecord) storePendingGameRefresh(scope, provider, { ...pending, job_id: status.job_id });
        publish(provider, { job: status, pending: !terminal || current !== null && !ownsRecord });
        if (status.status === "complete") completed.current();
      } catch (error) {
        if (!stopped && !controller.signal.aborted) {
          if (pending && (error instanceof FailedOperationError && error.operationId === pending.operation_id
              || !pending.job_id && error instanceof GameReviewRequestError && error.status === 422)) {
            if (read(provider)?.operation_id === pending.operation_id) clearPendingGameRefresh(scope, provider);
            pending = read(provider);
          }
          publish(provider, { pending: pending !== null,
            error: error instanceof PendingOperationError && !error.blocked ? undefined : error instanceof Error ? error.message : "Game refresh is unavailable." });
        }
      } finally {
        requests.delete(provider);
        if (!stopped && String(document.visibilityState) !== "hidden" && pending) {
          try { if (read(provider)) timers.set(provider, setTimeout(() => void run(provider), 2000)); }
          catch { /* The actionable recovery error above retains the original bytes. */ }
        }
      }
    };
    trigger.current = provider => { void run(provider, true); };
    const recover = () => {
      for (const provider of providers) {
        clearTimeout(timers.get(provider));
        if (document.visibilityState !== "hidden") void run(provider);
      }
    };
    recover();
    document.addEventListener("visibilitychange", recover);
    window.addEventListener("online", recover);
    return () => {
      stopped = true;
      trigger.current = () => undefined;
      for (const timer of timers.values()) clearTimeout(timer);
      for (const controller of requests.values()) controller.abort();
      document.removeEventListener("visibilitychange", recover);
      window.removeEventListener("online", recover);
    };
  }, [api]);
  return { progress, refresh: (provider: Provider) => trigger.current(provider) };
}
