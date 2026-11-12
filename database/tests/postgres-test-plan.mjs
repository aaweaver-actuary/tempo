// The executable plan is also the read-only --list output; keep one source of truth.
export function postgresTestStages({ mode, nativeChecks = [] }) {
  if (!Array.isArray(nativeChecks) || new Set(nativeChecks).size !== nativeChecks.length
    || nativeChecks.some(check => !['redis-fixture-isolation', 'position-grade-effects', 'position-introductions'].includes(check))
    || nativeChecks.length && !['study','browser'].includes(mode)) throw new Error('Invalid native PostgreSQL selection');
  if (!["full", "browser", "study", "durability", "lifecycle", "lifecycle-current"].includes(mode)) throw new Error(`Unknown PostgreSQL test mode: ${mode}`);
  if (mode === "study") return ["compose_config", "image_build", "startup", "service_health", "study_queue_receipts", "study_only", "study_durability", "study_isolation", ...nativeChecks, "browser", "cleanup"];
  if (mode === "lifecycle") {
    return ["compose_config", "image_build", "maintenance_cli", "deployment_lifecycle", "cleanup"];
  }
  if (mode === "lifecycle-current") {
    return ["compose_config", "image_build", "maintenance_cli", "deployment_lifecycle_current", "cleanup"];
  }
  const durabilityStages = [
    "background_budget", "operation_recovery", "schema_migrations", "background_diagnostics",
    ...(mode === "full" ? ["deployment_lifecycle"] : []), "background_workloads",
    "command_recreation", "backup_restore",
  ];
  return [
    "compose_config", "image_build", ...(mode === "browser" ? [] : ["maintenance_cli"]),
    "startup", "service_health", ...(mode === "browser" ? [] : ["game_review_reader", ...durabilityStages]),
    ...nativeChecks, ...(mode === "durability" ? [] : ["browser"]),
    ...(mode === "full" ? ["study_isolation"] : []),
    ...(mode === "browser" ? [] : ["study_durability"]), "cleanup",
  ];
}

export const backgroundWorkloadConsumers = ["background-worker", "background-scheduler"];

export async function restoreBackgroundWorkloadConsumers({ startConsumers, waitForReady, verifyConsumers }) {
  await startConsumers(["background-worker", "background-scheduler"]);
  await waitForReady();
  await verifyConsumers("running");
}

// The benchmark owns its synthetic rows until its cleanup completes. Real
// background consumers otherwise enqueue claims against those same eligible rows.
export async function executeIsolatedBackgroundWorkload({ stopConsumers, measureWorkload, restoreConsumers }) {
  let failure;
  try {
    await stopConsumers();
    await measureWorkload();
  } catch (error) {
    failure = error instanceof Error ? error : new Error(String(error));
  } finally {
    try { await restoreConsumers(); }
    catch (error) {
      failure = failure
        ? new AggregateError([failure, error], "Background workload and consumer restoration failed")
        : error instanceof Error ? error : new Error(String(error));
    }
  }
  if (failure) throw failure;
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

// Artifact capture must never prevent test-owned resource cleanup.
export async function executeDiagnosticCleanup(captureDiagnostics, cleanup) {
  let diagnosticFailure;
  try { await captureDiagnostics(); } catch (error) { diagnosticFailure = error; }
  try { await cleanup(); } catch (error) {
    if (diagnosticFailure) throw new AggregateError([diagnosticFailure, error], "Diagnostics and cleanup failed");
    throw error;
  }
  if (diagnosticFailure) throw diagnosticFailure;
}

// Resolve and validate every persistent target before a disposable stack mutates data.
export function assertDisposableVolumes(stack, project, volumes, mounts) {
  if (!/^tempo-pg-regressions-[a-z0-9-]+$/.test(project) || stack.name !== project)
    throw new Error("Disposable Compose project identity is invalid");
  const expected = new Set();
  for (const key of volumes) {
    const volume = stack.volumes?.[key];
    const name = `${project}_${key}`;
    if (!volume || volume.external || volume.name !== name || expected.has(name))
      throw new Error(`Unsafe disposable volume: ${key}`);
    expected.add(name);
  }
  if (Object.keys(stack.volumes ?? {}).length !== volumes.length) throw new Error("Unexpected disposable volume");
  for (const [service, target, source] of mounts) {
    const entries = (stack.services?.[service]?.volumes ?? []).filter(v => v.target === target);
    if (entries.length !== 1 || entries[0].type !== "volume" || entries[0].source !== source)
      throw new Error(`Unsafe disposable mount: ${service}:${target}`);
  }
}
