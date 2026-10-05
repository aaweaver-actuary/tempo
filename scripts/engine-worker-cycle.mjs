import { admitEngineJob, engineSearchWasPreempted } from "./engine-search.mjs";
import { recoverNextEngineJob } from "./engine-job-recovery.mjs";
import { engineWaitingStage } from "./engine-attempt-diagnostics.mjs";

// One production cycle: recover callbacks before claiming, search once, then publish or release.
export async function runEngineWorkerCycle({ searches, request, durableRequest, defenseClaimRequest, sleep,
  shutdown, logError = console.error }) {
  if (searches.fatalEngineError) { shutdown(); return; }
  let job;
  let jobKind;
  try {
    const recovered = await recoverNextEngineJob(durableRequest, defenseClaimRequest);
    ({ job, jobKind } = recovered);
    let { defenseClaimUnresolved } = recovered;
    if (!job) {
      const available = await request("/api/system/foreground-active");
      if (available.active) { engineWaitingStage("engine", "foreground_admission"); await sleep(2_000); return; }
      if (!defenseClaimUnresolved) {
        try {
          job = (await request("/api/defensive-threats/analysis/claim", { method: "POST" })).job;
        } catch (error) {
          if (!error.operationId) throw error;
          defenseClaimUnresolved = true;
          logError("Defensive claim remains unresolved:", error.operationId, error.message);
        }
      }
      if (job) jobKind = "defense";
      else {
        await request("/api/games/analysis/repair-timeout", { method: "POST" });
        await request("/api/games/analysis/repair-provenance", { method: "POST" });
        job = (await request("/api/games/analysis/position/claim", { method: "POST" })).job;
        jobKind = "game";
      }
    }
    if (!job) { engineWaitingStage("engine", "idle"); await sleep(2_000); return; }
    if (job.kind === "finalize") {
      await request("/api/games/analysis/position/finalize", {
        method: "POST", body: JSON.stringify({ lease_id: job.lease_id }),
      });
      job = undefined;
      return;
    }
    if (!await admitEngineJob(job, jobKind === "defense" ? "engine_defense" : "engine_game", request)) {
      job = undefined;
      return;
    }
    const { report, diagnostics } = await searches.evaluate(job, jobKind === "defense" ? "engine_defense" : "engine_game");
    await request(jobKind === "defense"
      ? `/api/defensive-threats/analysis/${job.id}/report`
      : `/api/games/analysis/position/${job.id}/report`, {
      method: "POST", body: JSON.stringify({ lease_id: job.lease_id, report, diagnostics }),
    });
  } catch (error) {
    if (error.operationId) {
      logError("Engine database command is still pending:", error.operationId);
      if (searches.fatalEngineError) { shutdown(); return; }
      await sleep(2_000);
      return;
    }
    if (job) {
      const preempted = engineSearchWasPreempted(error);
      try {
        if (job.kind === "finalize") {
          await request(`/api/games/analysis/${encodeURIComponent(job.game_id)}/failure`, {
            method: "POST", body: JSON.stringify({ lease_id: job.lease_id,
              error: `Game finalization failed: ${error.message.slice(0, 900)}` }),
          });
          job = undefined;
          return;
        }
        const prefix = jobKind === "defense" ? "/api/defensive-threats/analysis" : "/api/games/analysis/position";
        const deliveryController = searches.fatalEngineError ? new AbortController() : undefined;
        const deliveryDeadline = deliveryController ? setTimeout(() => deliveryController.abort(), 1_500) : undefined;
        try {
          await request(`${prefix}/${job.id}/${preempted ? "release" : "failure"}`, {
            method: "POST", body: JSON.stringify(preempted
              ? { lease_id: job.lease_id, diagnostics: error.diagnostics }
              : { lease_id: job.lease_id, error: error.message.slice(0, 1000), diagnostics: error.diagnostics }),
          }, deliveryController ? { signal: deliveryController.signal, pollAttempts: 0 } : undefined);
        } finally {
          clearTimeout(deliveryDeadline);
        }
      } catch (reportingError) { logError("Could not update engine request:", reportingError); }
    } else logError("Could not claim engine request:", error);
    if (searches.fatalEngineError) { shutdown(); return; }
    await sleep(2_000);
  }
}
