export const checkpoint1BrowserCases = [
  ['frozen-study-recovery-real.spec.ts', 'real frozen Study preserves original parent and child through reload and playable successor'],
  ['frozen-study-recovery-real.spec.ts', 'original frozen phone attempt survives offline reload and reconciles once'],
  ['manual-prefix-split-http.spec.ts', 'real manual split accepts original issued review and replays its exact receipt'],
  ['manual-prefix-split-http.spec.ts', 'real manual split rejection is retained on the original offered card'],
  ['minimal-game-review-acceptance.spec.ts', 'minimal Game Review compares pasted and saved games against current repertoire without Study writes'],
  ['opening-progression.spec.ts', 'one real short-prefix practice admits its child while parent is learning 390'],
  ['opening-progression.spec.ts', 'one real short-prefix practice admits its child while parent is learning 1280'],
  ['position-study-acceptance.spec.ts', 'position Study keeps its server-issued suffix and first-wrong journal across reload and offline'],
  ['study-only-mode.spec.ts', 'original review replay validator rejects failure and foreign receipts'],
  ['study-only-mode.spec.ts', 'study_only_mode_preserves_foreground_review_and_queue_progression across reload and resumption'],
];
export const checkpoint1BrowserGrep = checkpoint1BrowserCases.map(([, title]) => title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$').join('|');
export const checkpoint1NativeChecks = ['position-grade-effects', 'position-review-atomicity', 'position-introductions', 'position-queue-admission', 'prefix-split-placement', 'game-review-reader'];
export const checkpoint1QueueCases = [
  'test_postgres_queue_extraction_preserves_contention_restart_and_idempotent_replay',
  'test_postgres_scheduling_adapter_effects_rollback_with_receipt_failure',
  'test_postgres_scheduling_concurrent_replay_preserves_one_history_and_queue_effect',
  'test_retained_command_retry_preserves_immutable_identity',
  'test_postgres_paused_feedback_history_preserves_ordinary_study',
  'test_postgres_retained_review_reconciliation_preserves_original_attempt',
  'test_postgres_guided_marker_locks_displayed_revision_until_commit',
  'test_postgres_queue_attempt_maintenance_contention_restart_and_receipt_recovery',
];

export function verifyCheckpoint1QueueReceipt(receipt) {
  if (receipt?.case_set !== 'all' || receipt.scope !== 'all-eight' || receipt.execution !== 'completed' || receipt.executed_count !== checkpoint1QueueCases.length || JSON.stringify(receipt.cases) !== JSON.stringify(checkpoint1QueueCases) || new Set(receipt.cases).size !== checkpoint1QueueCases.length) throw new Error('Checkpoint1 requires exactly the eight completed queue cases');
}
export function verifyCheckpoint1BrowserReceipt(receipt) {
  const stats = receipt?.stats, cases = [];
  function visit(node) {
    for (const suite of node.suites ?? []) visit(suite);
    for (const spec of node.specs ?? []) for (const test of spec.tests ?? []) cases.push({ file: spec.file?.split('/').at(-1), title: spec.title, test });
  }
  visit(receipt ?? {});
  const identities = cases.map(item => JSON.stringify([item.file, item.title]));
  const expected = checkpoint1BrowserCases.map(item => JSON.stringify(item)).sort();
  if (stats?.expected !== expected.length || stats.skipped !== 0 || stats.unexpected !== 0 || stats.flaky !== 0 || cases.length !== expected.length || new Set(identities).size !== expected.length || JSON.stringify(identities.sort()) !== JSON.stringify(expected) || cases.some(({ test }) => test.projectName !== 'chromium' || test.status !== 'expected' || test.results?.length !== 1 || test.results[0].status !== 'passed' || test.results[0].retry !== 0)) throw new Error('Checkpoint1 requires exactly ten passing Chromium cases without skips or retries');
}

// The executable plan is also the read-only --list output; keep one source of truth.
export function postgresTestStages({ mode, nativeChecks = [], maintainedStudyProof = false }) {
  if (!Array.isArray(nativeChecks) || new Set(nativeChecks).size !== nativeChecks.length
    || nativeChecks.some(check => !['redis-fixture-isolation', 'position-grade-effects', 'position-review-atomicity', 'position-introductions', 'position-queue-admission', 'prefix-split-placement', 'game-review-reader'].includes(check))
    || nativeChecks.length && !['study','browser'].includes(mode)) throw new Error('Invalid native PostgreSQL selection');
  if (!["full", "browser", "study", "durability", "lifecycle", "lifecycle-current", "priority-historical"].includes(mode)) throw new Error(`Unknown PostgreSQL test mode: ${mode}`);
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
  if (mode === "priority-historical") return ["compose_config", "image_build", "priority_postgres_startup", "priority_historical_rollback", "cleanup"];
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

// A stage exit alone cannot establish that the HTTP split journey ran.
export function verifyStudyDurabilityScenario(report, scope) {
  if (report?.acceptance_scope !== scope || JSON.stringify(report.unresolved_owner_cases) !== JSON.stringify(unresolvedQueueCases) || report.historical_priority_rollback !== 'not executed') throw new Error('Wrong PostgreSQL acceptance scope or unresolved disposition');
  for (const label of ['prefix_split_http_accept_replay', 'prefix_split_http_restart_replay']) {
    const entries = (report.details ?? []).filter(entry => entry.stage === 'study_durability' && entry.label === label);
    if (entries.length !== 1 || entries[0].category !== 'scenario' || entries[0].completed !== true
        || entries[0].exit_code !== 0 || entries[0].skipped || entries[0].retries)
      throw new Error('Missing or invalid PostgreSQL HTTP split evidence');
  }
}

// Receipt URLs alone do not bind a returned operation to the requested one.
export function requireOriginalOperationReceipt(receipt, operationId) {
  if (!operationId || receipt?.operation_id !== operationId) throw new Error('Operation receipt identity changed');
  return receipt;
}

// A split archives the source and can remove its repertoire membership.
export function originalPrefixSplitRows(snapshot, sourceCardId) {
  if (!sourceCardId || !Array.isArray(snapshot?.tables?.prefix_splits)) throw new Error('Missing original split snapshot');
  return snapshot.tables.prefix_splits.filter(row => row.source_card_id === sourceCardId)
    .map(({source_card_id, source_revision, shortened_card_id, continuation_card_id}) =>
      ({source_card_id, source_revision, shortened_card_id, continuation_card_id}))
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}
