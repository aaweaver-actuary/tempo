import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeFastPlan, selectFastLane, validateFastGate, testIdentity, fastInventory } from '../../scripts/ci-fast-lane.mjs';
import { fastCommands } from '../../scripts/ci-fast-commands.mjs';
import { verifySelection } from '../../scripts/ci-fast-run.mjs';
import { changedPathsFromNameStatus } from '../../scripts/ci-verification-plan.mjs';
import { postgresTestStages, executePostgresTestPlan } from '../../scripts/postgres-test-plan.mjs';
import { parsePostgresTestOptions } from '../../scripts/postgres-test-options.mjs';
const context = {repository:'aaweaver-actuary/tempo',repositoryId:1,pullRequest:217,head:'a'.repeat(40),base:'b'.repeat(40)};
const pr = {number:217,state:'open',head:{sha:context.head},base:{sha:context.base,repo:{id:1,full_name:context.repository}}};
const execution = {id:123,attempt:1};
function fixture(path = 'app/lib/review-outbox.ts') {
  const plan = makeFastPlan([path],context);
  const report = {completed:true,status:'success',planHash:plan.hash,head:plan.head,repository:plan.repository,pullRequest:plan.pullRequest,runId:execution.id,attempt:1,runtime:{node:`v${plan.runtime.node}`,platform:plan.runtime.platform,architecture:plan.runtime.architecture}};
  report.commands = fastCommands(plan).map(([name,command,args]) => ({name,command,args,exit_code:0}));
  for (const layer of ['frontend','backend','browser']) {
    const selected = layer === 'browser' ? plan.browser : plan[layer];
    if (!selected.length) continue;
    const expected = selected.map(value => layer === 'browser' ? {id:testIdentity(value),identity:testIdentity(value)} : {id:`${value}::example`,file:value});
    report[layer] = {expected,tests:expected.map(value => ({...value,status:'passed',retries:0}))};
  }
  if (plan.tier === 'STUDY') {
    report.studyStages = postgresTestStages({mode:'study'});
    report.scenarios = {mode:'study',commit:plan.head,plan_hash:plan.hash,planned_stages:report.studyStages,
      stages:Object.fromEntries(report.studyStages.map(stage => [stage,{exit_code:0}]))};
  }
  return {plan,report,needs:{selection:{result:'success'},fast:{result:'success'}}};
}
const failures = fixture => validateFastGate(fixture.plan,fixture.report,fixture.needs,pr,execution);

test('emergency_docs_only_changes_do_not_trigger_full_qualification', () => {
  const selection = selectFastLane(['docs/testing.md','CONTRIBUTING.md']);
  assert.equal(selection.tier,'FAST'); assert.equal(selection.node,false); assert.equal(selection.python,false);
  assert.deepEqual(selection.frontend,[]); assert.deepEqual(selection.browser,[]);
});
test('emergency_isolated_frontend_change_selects_FAST', () => {
  const selection = selectFastLane(['app/components/prefix-comparison-dialog.tsx']);
  assert.equal(selection.tier,'FAST'); assert(selection.frontend.includes('tests/unit/prefix-comparison-regressions.test.tsx'));
  assert.equal(selection.browser.length,0);
});
test('emergency_tactics_grading_change_selects_STUDY', () => {
  assert.equal(selectFastLane(['backend/app/services/threat_grading.py']).tier,'STUDY');
  assert.equal(selectFastLane(['app/views/tactics_view.tsx']).tier,'STUDY');
});
test('emergency_queue_persistence_change_selects_STUDY', () => assert.equal(selectFastLane(['backend/app/services/postgres_queue_refresh.py']).tier,'STUDY'));
test('emergency_database_migration_selects_FULL', () => assert.equal(selectFastLane(['backend/migrations/999_emergency.sql']).tier,'FULL'));
test('emergency_qualification_validator_change_selects_FULL', () => assert.equal(selectFastLane(['scripts/ci-quality.mjs']).tier,'FULL'));
test('emergency_unknown_or_ambiguous_change_fails_closed_to_FULL', () => {
  assert.equal(selectFastLane(['app/new-component.tsx']).tier,'FULL');
  assert.equal(selectFastLane([],{comparisonAvailable:false}).tier,'FULL');
  const inventory = structuredClone(fastInventory); inventory.sources.push(inventory.sources.find(mapping => mapping.paths.includes('app/lib/review-outbox.ts')));
  assert.equal(selectFastLane(['app/lib/review-outbox.ts'],{inventory}).tier,'FULL');
});
test('emergency_missing_required_test_evidence_fails_merge_gate', () => {
  const evidence = fixture(); assert.deepEqual(failures(evidence),[]);
  delete evidence.report.frontend; assert(failures(evidence).length);
});
test('emergency_failed_selected_tests_cannot_report_success', () => {
  const evidence = fixture(); evidence.report.frontend.tests[0].status = 'failed'; assert(failures(evidence).length);
  const command = fixture(); command.report.commands[0].exit_code = 1; assert(failures(command).length);
});
test('emergency_stale_other_head_evidence_cannot_qualify', () => {
  const evidence = fixture(); evidence.report.head = 'c'.repeat(40); assert(failures(evidence).length);
  const current = fixture(); assert(validateFastGate(current.plan,current.report,current.needs,{...pr,head:{sha:'d'.repeat(40)}},execution).length);
});
test('emergency_rename_deletion_and_mixed_changes_preserve_highest_risk', () => {
  const paths = changedPathsFromNameStatus('R100\0backend/migrations/999_old.sql\0docs/new.md\0D\0scripts/ci-quality.mjs\0');
  assert.equal(selectFastLane(paths).tier,'FULL');
  assert.equal(selectFastLane(['app/components/prefix-comparison-dialog.tsx','app/lib/review-outbox.ts']).tier,'STUDY');
});
test('emergency_zero_skipped_duplicate_and_cancelled_selected_evidence_fails', () => {
  for (const mutation of [evidence => evidence.report.frontend.tests = [],evidence => evidence.report.frontend.tests[0].status = 'skipped',
    evidence => evidence.report.frontend.tests.push(evidence.report.frontend.tests[0]),evidence => evidence.needs.fast.result = 'cancelled',
    evidence => evidence.report.commands.pop(),evidence => delete evidence.report.scenarios]) {
    const evidence = fixture(); mutation(evidence); assert(failures(evidence).length);
  }
  assert.throws(() => verifySelection([],[]),/empty/);
});
test('emergency_base_only_advancement_preserves_FAST_and_STUDY_head_evidence', () => {
  for (const path of ['app/components/prefix-comparison-dialog.tsx','app/lib/review-outbox.ts']) {
    const evidence = fixture(path);
    assert.deepEqual(validateFastGate(evidence.plan,evidence.report,evidence.needs,{...pr,base:{...pr.base,sha:'c'.repeat(40)}},execution),[]);
  }
});
test('emergency_FULL_requires_existing_complete_quality', () => {
  const plan = makeFastPlan(['scripts/ci-quality.mjs'],context);
  assert(validateFastGate(plan,null,{selection:{result:'success'},quality:{result:'skipped'}},pr,execution).length);
  assert.deepEqual(validateFastGate(plan,null,{selection:{result:'success'},quality:{result:'success'}},pr,execution),[]);
});
test('emergency_cross_run_and_attempt_evidence_is_rejected', () => {
  for (const key of ['runId','attempt']) { const evidence = fixture(); evidence.report[key]++; assert(failures(evidence).length); }
});
test('emergency_study_mode_is_bounded_explicit_and_cleans_up_after_failure', async () => {
  assert.throws(() => parsePostgresTestOptions(['--mode','study']),/explicit/);
  assert.equal(parsePostgresTestOptions(['--mode','study','--browser-grep','smoke']).mode,'study');
  const stages = postgresTestStages({mode:'study'});
  for (const required of ['study_queue_receipts','study_only','study_durability','study_isolation','browser','cleanup']) assert(stages.includes(required));
  for (const excluded of ['deployment_lifecycle','backup_restore','background_workloads','maintenance_cli']) assert(!stages.includes(excluded));
  let cleaned = false;
  const actions = Object.fromEntries(stages.map(stage => [stage,async () => { if (stage === 'study_queue_receipts') throw new Error('infrastructure unavailable'); if (stage === 'cleanup') cleaned = true; }]));
  await assert.rejects(executePostgresTestPlan(stages,actions,async (_,action) => action()),/infrastructure/); assert(cleaned);
});
test('emergency_manifest_has_unique_existing_test_ownership_and_ten_smoke_cases', () => {
  const paths = fastInventory.sources.flatMap(mapping => mapping.paths);
  assert.equal(new Set(paths).size,paths.length); assert.equal(fastInventory.studyBrowser.length,10);
  for (const mapping of fastInventory.sources) for (const path of [...mapping.paths,...mapping.frontend,...mapping.backend]) assert(readFileSync(path).length);
});
test('emergency_workflow_runs_full_only_when_selected_and_pins_fast_head', () => {
  const workflow = readFileSync('.github/workflows/pages.yml','utf8');
  assert(workflow.includes("if: needs.selection.outputs.tier == 'FULL'"));
  assert(workflow.includes("if: always() && needs.plan.result == 'success' && (needs.plan.outputs.tier"));
  assert(workflow.includes('  merge-gate:\n    if: always()'));
  assert(workflow.includes('ref: ${{ needs.selection.outputs.head }}'));
  assert(workflow.includes('ci-verification-plan.mjs --base "$TEMPO_BASE" --complete'));
});

test('emergency_wrong_runtime_and_late_full_request_cannot_qualify', () => {
  const evidence = fixture(); evidence.report.runtime.architecture = 'x64'; assert(failures(evidence).length);
  const late = fixture(); assert(validateFastGate(late.plan,late.report,late.needs,{...pr,labels:[{name:'ci:full'}]},execution).length);
});

test('emergency_inactive_provider_adapter_selects_FAST_without_durability', () => {
  const selection = selectFastLane(['backend/app/services/lichess_client.py']);
  assert.equal(selection.tier,'FAST'); assert(selection.backend.includes('backend/tests/test_game_sync.py'));
  assert.equal(selection.browser.length,0); assert.equal(selection.python,true);
});

test('emergency_FULL_base_advancement_requires_fresh_integration', () => {
  const plan = makeFastPlan(['backend/migrations/999_new.sql'],context);
  assert(validateFastGate(plan,null,{selection:{result:'success'},quality:{result:'success'}},{...pr,base:{...pr.base,sha:'c'.repeat(40)}},execution).length);
});
