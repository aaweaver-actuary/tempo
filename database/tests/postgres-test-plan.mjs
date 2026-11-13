// The executable plan is also the read-only --list output; keep one source of truth.
export function postgresTestStages({ mode, nativeChecks = [], maintainedStudyProof = false }) {
  if (!Array.isArray(nativeChecks) || new Set(nativeChecks).size !== nativeChecks.length
    || nativeChecks.some(check => !['redis-fixture-isolation', 'position-grade-effects', 'position-introductions', 'position-queue-admission', 'prefix-split-placement', 'game-review-reader'].includes(check))
    || nativeChecks.length && !['study','browser'].includes(mode)) throw new Error('Invalid native PostgreSQL selection');
  if (!["full", "browser", "study", "durability", "lifecycle", "lifecycle-current"].includes(mode)) throw new Error(`Unknown PostgreSQL test mode: ${mode}`);
  const nativeStages = nativeChecks.map(check => check === "game-review-reader" ? "game_review_reader" : check);
  if (maintainedStudyProof) {
    if (mode !== "study" || nativeChecks.length) throw new Error("Invalid maintained Study proof selection");
    return ["compose_config", "image_build", "startup", "service_health", "maintained_study_queue_receipts", "maintained_study_only", "study_durability", "study_isolation", "browser", "cleanup"];
  }
  if (mode === "study") return ["compose_config", "image_build", "startup", "service_health", "study_queue_receipts", "study_only", "study_durability", "study_isolation", ...nativeStages, "browser", "cleanup"];
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
    ...nativeStages, ...(mode === "durability" ? [] : ["browser"]),
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

// Fixed partial acceptance contract, not a configurable case-selection framework.
export const maintainedQueueCases = [
  'test_postgres_queue_extraction_preserves_contention_restart_and_idempotent_replay',
  'test_postgres_scheduling_adapter_effects_rollback_with_receipt_failure',
  'test_postgres_scheduling_concurrent_replay_preserves_one_history_and_queue_effect',
  'test_retained_command_retry_preserves_immutable_identity',
  'test_postgres_guided_marker_locks_displayed_revision_until_commit',
  'test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery',
];
export const unresolvedQueueCases = [
  'test_postgres_retained_review_reconciliation_preserves_original_attempt',
];
export const excludedQueueCases = [
  'test_postgres_paused_feedback_history_preserves_ordinary_study',
  ...unresolvedQueueCases,
];
export function verifyMaintainedQueueReceipt(receipt) {
  if (receipt?.case_set !== 'maintained-six' || receipt.scope !== 'partial'
      || receipt.execution !== 'completed' || receipt.executed_count !== 6
      || JSON.stringify(receipt.cases) !== JSON.stringify(maintainedQueueCases)
      || JSON.stringify(receipt.excluded_cases) !== JSON.stringify(excludedQueueCases)
      || JSON.stringify(receipt.unresolved_owner_cases) !== JSON.stringify(unresolvedQueueCases))
    throw new Error('Maintained queue proof requires all six exact cases and unresolved-owner disposition');
}
export function verifyMaintainedBrowserReceipt(receipt) {
  const stats = receipt?.stats;
  const results = [];
  function visit(node) {
    for (const suite of node.suites ?? []) visit(suite);
    for (const spec of node.specs ?? []) for (const test of spec.tests ?? [])
      for (const result of test.results ?? []) results.push({title: spec.title, ...result});
  }
  visit(receipt ?? {});
  if (stats?.expected !== 1 || stats.skipped !== 0 || stats.unexpected !== 0 || stats.flaky !== 0
      || results.length !== 1 || results[0].status !== 'passed' || results[0].retry !== 0
      || results[0].title !== 'study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption')
    throw new Error('Maintained Study browser proof requires exactly one real passing case without skips or retries');
}
