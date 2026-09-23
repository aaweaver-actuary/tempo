import { useEffect } from "react";
import { API_URL } from "../const";
import { coverageMaiaClaimSchema } from "../domain/schemas";
import { requestBackgroundMaia } from "../lib/maia-broker";
import { readJsonResponse } from "../lib/validated-data";
import { usesLocalApi } from "../utils/local";
import { backgroundFetch } from "../lib/background-fetch";
import { reportDebugError } from "../lib/debug-reporting";
import { onLichessSessionTokenChange, readLichessSessionToken } from "../lib/lichess-session";

const IDLE_DELAY_MS = 3_000;
const RETRY_DELAY_MS = 15_000;

export function useRepertoireCoverageWorker() {
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let running = false;
    let lastForegroundActivity = Date.now();
    let timer: number | undefined;
    let heartbeatTimer: number | undefined;
    let activeRunId: string | undefined;
    let activeLease: { nodeId: string; leaseId: string } | undefined;
    let activeController: AbortController | undefined;
    let sessionRegistration: Promise<void> = Promise.resolve();
    let registeredSessionToken: string | null | undefined;
    const registerExplorerSession = () => {
      const token = readLichessSessionToken();
      sessionRegistration = sessionRegistration.catch(() => undefined).then(async () => {
        const response = await backgroundFetch(`${API_URL}/api/repertoire-coverage/explorer-session`, {
          method: "POST",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (response.ok) registeredSessionToken = token || null;
      }).catch(() => undefined);
    };
    registerExplorerSession();
    const removeTokenListener = onLichessSessionTokenChange(registerExplorerSession);
    const clearExplorerSessionOnPageExit = () => {
      void fetch(`${API_URL}/api/repertoire-coverage/explorer-session`, {
        method: "POST",
        keepalive: true,
        mode: "no-cors",
      }).catch(() => undefined);
    };
    const releaseActiveLease = () => {
      const lease = activeLease;
      activeLease = undefined;
      activeController?.abort();
      if (lease) void backgroundFetch(`${API_URL}/api/repertoire-coverage/maia/release`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: lease.nodeId, lease_id: lease.leaseId }),
      }).catch(() => undefined);
    };
    const handleControl = (event: Event) => {
      const detail = (event as CustomEvent<{source: string; id: string; action: string}>).detail;
      if (detail?.source === "coverage" && detail.id === activeRunId && detail.action === "pause")
        releaseActiveLease();
      if (detail?.source === "coverage" && detail.action === "resume") schedule(0);
    };

    const schedule = (delay: number) => {
      if (!stopped) timer = window.setTimeout(() => void run(), delay);
    };
    const recordForegroundActivity = () => {
      lastForegroundActivity = Date.now();
    };
    async function run() {
      if (stopped || running) return;
      if (
        document.visibilityState !== "visible" ||
        Date.now() - lastForegroundActivity < IDLE_DELAY_MS
      ) {
        schedule(IDLE_DELAY_MS);
        return;
      }
      running = true;
      try {
        await sessionRegistration;
        if (registeredSessionToken !== readLichessSessionToken()) {
          registerExplorerSession();
          await sessionRegistration;
        }
        const claimResponse = await backgroundFetch(
          `${API_URL}/api/repertoire-coverage/maia/claim`,
          { method: "POST" },
        );
        const { job } = await readJsonResponse(
          claimResponse,
          coverageMaiaClaimSchema,
          "repertoire coverage MAIA claim",
        );
        if (!job) {
          schedule(RETRY_DELAY_MS);
          return;
        }
        activeRunId = job.run_id;
        activeLease = { nodeId: job.node_id, leaseId: job.lease_id };
        activeController = new AbortController();
        heartbeatTimer = window.setInterval(() => {
          if (!activeLease) return;
          void backgroundFetch(`${API_URL}/api/repertoire-coverage/maia/heartbeat`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ node_id: activeLease.nodeId, lease_id: activeLease.leaseId }),
          }).then(response => { if (response.status === 409) releaseActiveLease(); })
            .catch(() => undefined);
        }, 2_000);
        const moves = await requestBackgroundMaia(job.fen, job.elo, undefined, activeController.signal);
        if (activeController.signal.aborted) { schedule(RETRY_DELAY_MS); return; }
        const submitResponse = await backgroundFetch(`${API_URL}/api/repertoire-coverage/maia/submit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            node_id: job.node_id,
            lease_id: job.lease_id,
            moves: moves
              .filter((move) => move.probability !== undefined)
              .map((move) => ({
                move_uci: move.uci,
                probability: move.probability,
              })),
          }),
        });
        if (submitResponse.status === 409) { schedule(RETRY_DELAY_MS); return; }
        if (!submitResponse.ok) throw new Error(`Coverage submission failed: HTTP ${submitResponse.status}`);
        activeLease = undefined;
        schedule(1_000);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") { schedule(RETRY_DELAY_MS); return; }
        reportDebugError(error, {
          kind: "api",
          source: "background-repertoire-coverage",
          operation: "refresh repertoire coverage",
          endpoint: `${API_URL}/api/repertoire-coverage/maia/claim`,
          method: "POST",
        });
        schedule(RETRY_DELAY_MS);
      } finally {
        if (heartbeatTimer !== undefined) { window.clearInterval(heartbeatTimer); heartbeatTimer = undefined; }
        if (activeLease) releaseActiveLease();
        activeRunId = undefined;
        activeController = undefined;
        running = false;
      }
    }
    window.addEventListener("pointerdown", recordForegroundActivity, true);
    window.addEventListener("keydown", recordForegroundActivity, true);
    document.addEventListener("visibilitychange", recordForegroundActivity);
    window.addEventListener("tempo:background-control", handleControl);
    window.addEventListener("pagehide", clearExplorerSessionOnPageExit);
    schedule(IDLE_DELAY_MS);
    return () => {
      stopped = true;
      removeTokenListener();
      window.removeEventListener("pagehide", clearExplorerSessionOnPageExit);
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("pointerdown", recordForegroundActivity, true);
      window.removeEventListener("keydown", recordForegroundActivity, true);
      document.removeEventListener("visibilitychange", recordForegroundActivity);
      window.removeEventListener("tempo:background-control", handleControl);
      releaseActiveLease();
    };
  }, []);
}
