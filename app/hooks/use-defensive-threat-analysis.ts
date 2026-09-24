import { useEffect } from "react";
import { Chess } from "chess.js";
import { API_URL } from "../const";
import { backgroundFetch } from "../lib/background-fetch";
import { requestBackgroundStockfishRequest } from "../lib/engine-broker";
import { asFenString } from "../types";
import { usesLocalApi } from "../utils/local";

type ThreatAnalysisJob = {
  id: string;
  lease_id: string;
  request: {
    position_start_fen: string;
    position_prefix_uci: string[];
    engine_version: string;
    network_version: string;
    depth: number;
    multipv: number;
    root_move_uci: string | null;
  };
};

const ENGINE_VERSION = "Stockfish 19 WASM";
const NETWORK_VERSION = "nn-61e7af4bb97d.nnue";
const POLL_INTERVAL_MS = 10_000;
const IDLE_DELAY_MS = 2_000;

function positionForRequest(job: ThreatAnalysisJob) {
  const board = new Chess(job.request.position_start_fen);
  for (const moveUci of job.request.position_prefix_uci) {
    board.move({
      from: moveUci.slice(0, 2),
      to: moveUci.slice(2, 4),
      promotion: moveUci[4],
    });
  }
  if (job.request.root_move_uci) {
    const rootMove = job.request.root_move_uci;
    const legal = board.moves({ verbose: true }).some((move) =>
      `${move.from}${move.to}${move.promotion ?? ""}` === rootMove,
    );
    if (!legal) throw new Error("Requested root move is illegal in its full history");
  }
  return board;
}

export function useDefensiveThreatAnalysis() {
  useEffect(() => {
    if (!usesLocalApi()) return;
    let stopped = false;
    let running = false;
    let timer: number | undefined;
    let lastActivity = Date.now();
    let active: { job: ThreatAnalysisJob; controller: AbortController } | undefined;

    const schedule = (delay: number) => {
      if (stopped) return;
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => void run(), delay);
    };
    const release = async (job: ThreatAnalysisJob) => {
      await backgroundFetch(`${API_URL}/api/defensive-threats/analysis/${job.id}/release`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lease_id: job.lease_id }),
      });
    };
    const onActivity = () => {
      lastActivity = Date.now();
      active?.controller.abort();
    };
    const onVisibility = () => {
      onActivity();
      if (document.visibilityState === "visible") schedule(IDLE_DELAY_MS);
    };

    async function run() {
      if (stopped || running) return;
      const idleRemaining = IDLE_DELAY_MS - (Date.now() - lastActivity);
      if (document.visibilityState !== "visible" || idleRemaining > 0) {
        schedule(Math.max(IDLE_DELAY_MS, idleRemaining));
        return;
      }
      running = true;
      let job: ThreatAnalysisJob | undefined;
      try {
        const claimResponse = await backgroundFetch(`${API_URL}/api/defensive-threats/analysis/claim`, {
          method: "POST",
        });
        if (!claimResponse.ok) throw new Error("Could not claim defensive analysis");
        const claimed = await claimResponse.json() as { job: ThreatAnalysisJob | null };
        job = claimed.job ?? undefined;
        if (!job) return;
        if (document.visibilityState !== "visible" ||
            Date.now() - lastActivity < IDLE_DELAY_MS) {
          await release(job);
          return;
        }
        if (job.request.engine_version !== ENGINE_VERSION ||
            job.request.network_version !== NETWORK_VERSION) {
          throw new Error("Defensive analysis requires a different engine version");
        }
        const board = positionForRequest(job);
        const controller = new AbortController();
        active = { job, controller };
        const moves = await requestBackgroundStockfishRequest({
          fen: asFenString(board.fen()),
          depth: job.request.depth,
          multipv: job.request.multipv,
          positionStartFen: job.request.position_start_fen,
          positionPrefixUci: job.request.position_prefix_uci,
          rootMoveUci: job.request.root_move_uci ?? undefined,
        }, controller.signal);
        if (controller.signal.aborted) {
          await release(job);
          return;
        }
        const whiteSign = board.turn() === "w" ? 1 : -1;
        const scoredMoves = moves.filter((move) => move.cp !== undefined || move.mate !== undefined);
        const report = {
          request: job.request,
          complete: scoredMoves.length > 0,
          lines: scoredMoves.map((move) => ({
            root_move_uci: move.uci,
            pv_uci: move.pv ?? [move.uci],
            score: move.mate === undefined
              ? { cp: move.cp === undefined ? null : move.cp * whiteSign, mate: null }
              : { cp: null, mate: move.mate * whiteSign },
            depth: move.depth ?? 0,
          })),
        };
        const response = await backgroundFetch(
          `${API_URL}/api/defensive-threats/analysis/${job.id}/report`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lease_id: job.lease_id, report }),
          },
        );
        if (!response.ok) throw new Error(`Could not save defensive analysis (${response.status})`);
      } catch (error) {
        if (job) {
          if (active?.controller.signal.aborted) {
            await release(job).catch(() => undefined);
          } else {
            await backgroundFetch(`${API_URL}/api/defensive-threats/analysis/${job.id}/failure`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                lease_id: job.lease_id,
                error: error instanceof Error ? error.message : "Defensive analysis failed",
              }),
            }).catch(() => undefined);
          }
        }
      } finally {
        active = undefined;
        running = false;
        schedule(POLL_INTERVAL_MS);
      }
    }

    document.addEventListener("pointerdown", onActivity);
    document.addEventListener("keydown", onActivity);
    document.addEventListener("visibilitychange", onVisibility);
    schedule(IDLE_DELAY_MS);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      active?.controller.abort();
      document.removeEventListener("pointerdown", onActivity);
      document.removeEventListener("keydown", onActivity);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
}
