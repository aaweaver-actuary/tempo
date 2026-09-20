import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { gameAnalysisClaimSchema } from "../domain/schemas";
import { requestBackgroundAnalysis } from "../lib/engine-broker";
import { scanGameTwoPass } from "../lib/game-scan";
import { readJsonResponse } from "../lib/validated-data";
import { invalidateWorkspaceData } from "../lib/workspace-data";
import { usesLocalApi } from "../utils/local";

const LOCAL_IDLE_DELAY_MS = 1_500;
const EMPTY_QUEUE_RETRY_MS = 15_000;
const BUSY_RETRY_MS = 2_000;
const LEASE_HEARTBEAT_MS = 60_000;
const ANALYSIS_EVIDENCE_VERSION = 2;

type ActiveLease = { gameId: string; leaseId: string };

async function updateLease(
  lease: ActiveLease,
  action: "heartbeat" | "release",
) {
  await fetch(
    `${API_URL}/api/games/analysis/${encodeURIComponent(lease.gameId)}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lease_id: lease.leaseId }),
    },
  );
}

export function useGameAnalysis() {
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let running = false;
    let retryTimer: number | undefined;
    let heartbeatTimer: number | undefined;
    let activeLease: ActiveLease | undefined;
    let analysisController: AbortController | undefined;
    let lastForegroundActivity = Date.now();

    const schedule = (delay: number) => {
      if (stopped) return;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(() => void run(), delay);
    };
    const recordForegroundActivity = () => {
      lastForegroundActivity = Date.now();
    };
    const releaseActiveLease = () => {
      const lease = activeLease;
      activeLease = undefined;
      analysisController?.abort();
      analysisController = undefined;
      if (lease) void updateLease(lease, "release").catch(() => undefined);
    };
    const visibilityChanged = () => {
      recordForegroundActivity();
      if (document.visibilityState !== "visible") releaseActiveLease();
      else schedule(LOCAL_IDLE_DELAY_MS);
    };

    async function run() {
      if (stopped || running) return;
      const idleFor = Date.now() - lastForegroundActivity;
      if (document.visibilityState !== "visible" || idleFor < LOCAL_IDLE_DELAY_MS) {
        schedule(Math.max(BUSY_RETRY_MS, LOCAL_IDLE_DELAY_MS - idleFor));
        return;
      }
      running = true;
      try {
        const claimResponse = await fetch(`${API_URL}/api/games/analysis/claim`, {
          method: "POST",
        });
        const { job } = await readJsonResponse(
          claimResponse,
          gameAnalysisClaimSchema,
          "game analysis claim",
        );
        if (!job) {
          setStatus("");
          schedule(EMPTY_QUEUE_RETRY_MS);
          return;
        }
        activeLease = { gameId: job.game_id, leaseId: job.lease_id };
        analysisController = new AbortController();
        const signal = analysisController.signal;
        heartbeatTimer = window.setInterval(() => {
          if (activeLease)
            void updateLease(activeLease, "heartbeat").catch(() => undefined);
        }, LEASE_HEARTBEAT_MS);
        setStatus(`Analyzing ${job.provider} game…`);
        try {
          const evaluations = await scanGameTwoPass(
            job.start_fen,
            job.moves,
            job.color,
            (fen, depth) => requestBackgroundAnalysis(fen, depth, signal),
            job.divergence_ply,
            signal,
          );
          const submitResponse = await fetch(
            `${API_URL}/api/games/${encodeURIComponent(job.game_id)}/analysis`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                evaluations,
                depth: 14,
                lease_id: job.lease_id,
                idempotency_key: `${job.game_id}:analysis:${job.analysis_version}:evidence:${ANALYSIS_EVIDENCE_VERSION}`,
                analysis_version: job.analysis_version,
                analysis_evidence_version: ANALYSIS_EVIDENCE_VERSION,
                engine_version: "Stockfish 19 WASM",
                network_version: "nn-1c0000000000.nnue",
              }),
              signal,
            },
          );
          if (!submitResponse.ok)
            throw new Error("Could not save the completed game analysis.");
          activeLease = undefined;
          invalidateWorkspaceData();
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") {
            releaseActiveLease();
          } else if (activeLease) {
            const failedLease = activeLease;
            activeLease = undefined;
            await fetch(
              `${API_URL}/api/games/analysis/${encodeURIComponent(job.game_id)}/failure`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  lease_id: failedLease.leaseId,
                  error:
                    error instanceof Error
                      ? error.message
                      : "Game analysis failed",
                }),
              },
            );
          }
        }
        schedule(BUSY_RETRY_MS);
      } catch (error) {
        if (!stopped) {
          setStatus(
            error instanceof Error
              ? `Game analysis paused: ${error.message}`
              : "Game analysis paused",
          );
          schedule(EMPTY_QUEUE_RETRY_MS);
        }
      } finally {
        running = false;
        if (heartbeatTimer !== undefined) {
          window.clearInterval(heartbeatTimer);
          heartbeatTimer = undefined;
        }
      }
    }

    window.addEventListener("pointerdown", recordForegroundActivity, true);
    window.addEventListener("keydown", recordForegroundActivity, true);
    document.addEventListener("visibilitychange", visibilityChanged);
    schedule(LOCAL_IDLE_DELAY_MS);
    return () => {
      stopped = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      if (heartbeatTimer !== undefined) window.clearInterval(heartbeatTimer);
      window.removeEventListener("pointerdown", recordForegroundActivity, true);
      window.removeEventListener("keydown", recordForegroundActivity, true);
      document.removeEventListener("visibilitychange", visibilityChanged);
      releaseActiveLease();
    };
  }, []);
  return status;
}
