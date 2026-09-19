import { useEffect } from "react";
import { API_URL } from "../const";
import { coverageMaiaClaimSchema } from "../domain/schemas";
import { analyzeWithMaia } from "../lib/analysis-engines";
import { readJsonResponse } from "../lib/validated-data";
import { usesLocalApi } from "../utils/local";

const IDLE_DELAY_MS = 3_000;
const RETRY_DELAY_MS = 15_000;

export function useRepertoireCoverageWorker() {
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let running = false;
    let lastForegroundActivity = Date.now();
    let timer: number | undefined;

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
        const claimResponse = await fetch(
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
        const moves = await analyzeWithMaia(job.fen, job.elo);
        await fetch(`${API_URL}/api/repertoire-coverage/maia/submit`, {
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
        schedule(1_000);
      } catch {
        schedule(RETRY_DELAY_MS);
      } finally {
        running = false;
      }
    }
    window.addEventListener("pointerdown", recordForegroundActivity, true);
    window.addEventListener("keydown", recordForegroundActivity, true);
    document.addEventListener("visibilitychange", recordForegroundActivity);
    schedule(IDLE_DELAY_MS);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("pointerdown", recordForegroundActivity, true);
      window.removeEventListener("keydown", recordForegroundActivity, true);
      document.removeEventListener("visibilitychange", recordForegroundActivity);
    };
  }, []);
}
