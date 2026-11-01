// Select one recovered job per cycle so a second journal cannot be cleared early.
export async function recoverNextEngineJob(game, defense) {
  const recoveredGame = await game.recover();
  if (recoveredGame)
    return { job: recoveredGame, family: game, defenseClaimUnresolved: false };
  try {
    const recoveredDefense = await defense.recover();
    if (recoveredDefense?.job)
      return { job: recoveredDefense.job, family: defense, defenseClaimUnresolved: false };
    return { job: null, family: undefined, defenseClaimUnresolved: false,
      ...(recoveredDefense?.preferred_game_id ? { preferredGameId: recoveredDefense.preferred_game_id } : {}) };
  } catch (error) {
    if (!error.operationId) throw error;
    console.error("Defensive claim remains unresolved:", error.operationId, error.message);
    return { job: null, family: undefined, defenseClaimUnresolved: true };
  }
}
