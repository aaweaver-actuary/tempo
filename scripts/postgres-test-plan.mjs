// The executable plan is also the read-only --list output; keep one source of truth.
export function postgresTestStages({ mode }) {
  if (!["full", "browser", "durability"].includes(mode)) throw new Error(`Unknown PostgreSQL test mode: ${mode}`);
  const durabilityStages = [
    "background_budget", "operation_recovery", "schema_upgrade", "background_workloads",
    "threat_candidate_upsert", "command_recreation", "backup_restore",
  ];
  return [
    "compose_config", "image_build", ...(mode === "browser" ? [] : ["maintenance_cli"]),
    "startup", "service_health", ...(mode === "browser" ? [] : durabilityStages),
    ...(mode === "durability" ? [] : ["browser"]),
    ...(mode === "browser" ? [] : ["study_durability"]), "cleanup",
  ];
}

// Inject actions so the real selection, failure, and cleanup behavior can be
// regression-tested without starting Docker or weakening the product gate.
export async function executePostgresTestPlan(stages, actions, measure, onFailure = () => {}) {
  let failure;
  try {
    if (stages.at(-1) !== "cleanup" || new Set(stages).size !== stages.length) {
      throw new Error("PostgreSQL plan must contain unique stages ending in cleanup");
    }
    for (const stage of stages) {
      if (typeof actions[stage] !== "function") throw new Error(`Missing PostgreSQL test action: ${stage}`);
    }
    for (const stage of stages.slice(0, -1)) await measure(stage, actions[stage]);
  } catch (error) {
    failure = error instanceof Error ? error : new Error(String(error));
    try { await onFailure(error); }
    catch (diagnosticError) { failure = new AggregateError([error, diagnosticError], "PostgreSQL tests and diagnostics failed"); }
  } finally {
    try { await measure("cleanup", actions.cleanup); }
    catch (cleanupError) {
      failure = failure
        ? new AggregateError([failure, cleanupError], "PostgreSQL tests and cleanup failed")
        : cleanupError instanceof Error ? cleanupError : new Error(String(cleanupError));
    }
  }
  if (failure) throw failure;
}
