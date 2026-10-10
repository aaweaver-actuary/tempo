// Select one recovered job per engine cycle so clearing one journal cannot lose another job.
export async function recoverNextEngineJob(gameJournal, defenseJournal) {
  const recoveredGame = await gameJournal.recover();
  if (recoveredGame?.path === "/api/games/analysis/position/claim" && recoveredGame.result.job)
    return { job: recoveredGame.result.job, jobKind: "game", defenseClaimUnresolved: false };
  let preferredGameId;
  try {
    const recoveredDefense = await defenseJournal.recover();
    preferredGameId = recoveredDefense?.result.preferred_game_id;
    if (recoveredDefense?.result.job)
      return { job: recoveredDefense.result.job, jobKind: "defense", defenseClaimUnresolved: false };
  } catch (error) {
    if (!error.operationId) throw error;
    console.error("Defensive claim remains unresolved:", error.operationId, error.message);
    return { job: null, jobKind: undefined, defenseClaimUnresolved: true };
  }
  return { job: null, jobKind: undefined, defenseClaimUnresolved: false,
    ...(preferredGameId ? { preferredGameId } : {}) };
}
