import { admitEngineJob, engineSearchWasPreempted } from "./engine-search.mjs";
import { recoverNextEngineJob } from "./engine-job-recovery.mjs";
import { createEngineJobFamilies } from "./engine-job-families.mjs";
import { engineWaitingStage } from "./engine-attempt-diagnostics.mjs";

// One production cycle: recover callbacks before claiming, search once, then publish or release.
export async function runEngineWorkerCycle({ searches, request, durableRequest, defenseClaimRequest, sleep,
  shutdown, logError = console.error }) {
  if (searches.fatalEngineError) { shutdown(); return; }
  let job;
  let family;
  const families = createEngineJobFamilies({ request, durableRequest, defenseClaimRequest });
  try {
    const recovered = await recoverNextEngineJob(families.game, families.defense);
    ({ job, family } = recovered);
    let { defenseClaimUnresolved, preferredGameId } = recovered;
    if (!job) {
      const available = await request("/api/system/foreground-active");
      if (available.study_only_mode) { engineWaitingStage("engine", "study_only_mode"); await sleep(5_000); return; }
      if (available.available === false || available.active !== false) { engineWaitingStage("engine", "foreground_admission"); await sleep(2_000); return; }
      if (!defenseClaimUnresolved) {
        try {
          const selection = await families.defense.claim();
          job = selection.job;
          preferredGameId = selection.preferred_game_id;
        } catch (error) {
          if (!error.operationId) throw error;
          defenseClaimUnresolved = true;
          logError("Defensive claim remains unresolved:", error.operationId, error.message);
        }
      }
      if (job) family = families.defense;
      else {
        job = await families.game.claim(preferredGameId);
        family = families.game;
      }
    }
    if (!job) { engineWaitingStage("engine", "idle"); await sleep(2_000); return; }
    if (family.isFinalize(job)) {
      await family.finalize(job);
      job = undefined;
      return;
    }
    if (!await admitEngineJob(job, family.workClass, request)) {
      job = undefined;
      return;
    }
    const { report, diagnostics } = await searches.evaluate(job, family.workClass);
    await family.complete(job, report, diagnostics);
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
        if (family.isFinalize(job)) {
          await family.finalizeFailure(job, error);
          job = undefined;
          return;
        }
        const deliveryController = searches.fatalEngineError ? new AbortController() : undefined;
        const deliveryDeadline = deliveryController ? setTimeout(() => deliveryController.abort(), 1_500) : undefined;
        try {
          const deliveryOptions = deliveryController ? { signal: deliveryController.signal, pollAttempts: 0 } : undefined;
          await (preempted ? family.release(job, error, deliveryOptions) : family.fail(job, error, deliveryOptions));
        } finally {
          clearTimeout(deliveryDeadline);
        }
      } catch (reportingError) { logError("Could not update engine request:", reportingError); }
    } else logError("Could not claim engine request:", error);
    if (searches.fatalEngineError) { shutdown(); return; }
    await sleep(2_000);
  }
}
