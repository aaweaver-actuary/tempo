import { useEffect, useState } from "react";
import { API_URL } from "../const";
import { gameAnalysisClaimSchema } from "../domain/schemas";
import { requestBackgroundAnalysis } from "../lib/engine-broker";
import { scanGameTwoPass } from "../lib/game-scan";
import { readJsonResponse } from "../lib/validated-data";
import { invalidateWorkspaceData } from "../lib/workspace-data";
import { usesLocalApi } from "../utils/local";

export function useGameAnalysis() {
  const [status, setStatus] = useState("");
  useEffect(() => {
    if (!usesLocalApi()) return;
    const controller = new AbortController();
    let retryTimer: number | undefined;
    async function run() {
      try {
        const claimResponse = await fetch(`${API_URL}/api/games/analysis/claim`, {
          method: "POST",
          signal: controller.signal,
        });
        const { job } = await readJsonResponse(claimResponse, gameAnalysisClaimSchema, "game analysis claim");
        if (!job) {
          setStatus("");
          retryTimer = window.setTimeout(() => void run(), 15_000);
          return;
        }
        setStatus(`Analyzing ${job.provider} game…`);
        try {
          const evaluations = await scanGameTwoPass(
            job.start_fen,
            job.moves,
            job.color,
            requestBackgroundAnalysis,
            job.divergence_ply,
            controller.signal,
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
                idempotency_key: `${job.game_id}:analysis:${job.analysis_version}`,
                analysis_version: job.analysis_version,
                engine_version: "Stockfish 19 WASM",
                network_version: "nn-1c0000000000.nnue",
              }),
              signal: controller.signal,
            },
          );
          if (!submitResponse.ok) throw new Error("Could not save the completed game analysis.");
          invalidateWorkspaceData();
        } catch (error) {
          if (controller.signal.aborted) return;
          await fetch(`${API_URL}/api/games/analysis/${encodeURIComponent(job.game_id)}/failure`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              lease_id: job.lease_id,
              error: error instanceof Error ? error.message : "Game analysis failed",
            }),
          });
        }
        if (!controller.signal.aborted) queueMicrotask(() => void run());
      } catch (error) {
        if (controller.signal.aborted) return;
        setStatus(error instanceof Error ? `Game analysis paused: ${error.message}` : "Game analysis paused");
        retryTimer = window.setTimeout(() => void run(), 15_000);
      }
    }
    void run();
    return () => {
      controller.abort();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, []);
  return status;
}
