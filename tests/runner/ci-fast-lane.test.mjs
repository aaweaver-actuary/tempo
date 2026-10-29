import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';
import { makeFastPlan, selectFastLane, validateFastGate, testIdentity, fastInventory } from '../../scripts/ci-fast-lane.mjs';
import { fastCommands } from '../../scripts/ci-fast-commands.mjs';
import { verifySelection } from '../../scripts/ci-fast-run.mjs';
import { changedPathsFromNameStatus, planHash } from '../../scripts/ci-verification-plan.mjs';
import { postgresTestStages, executePostgresTestPlan } from '../../database/tests/postgres-test-plan.mjs';
import { parsePostgresTestOptions } from '../../database/tests/postgres-test-options.mjs';
import { certificate, readCertificateSources } from '../../scripts/ci-certified-tests.mjs';
const context = {repository:'aaweaver-actuary/tempo',repositoryId:1,pullRequest:217,head:'a'.repeat(40),base:'b'.repeat(40),draft:false};
const pr = {number:217,state:'open',draft:false,merge_commit_sha:'e'.repeat(40),head:{sha:context.head},base:{sha:context.base,repo:{id:1,full_name:context.repository}}};
const execution = {id:123,attempt:1};
function fixture(path = 'app/lib/review-outbox.ts') {
  const plan = makeFastPlan([path],context);
  const report = {completed:true,status:'success',planHash:plan.hash,head:plan.head,repository:plan.repository,pullRequest:plan.pullRequest,runId:execution.id,attempt:1,runtime:{node:`v${plan.runtime.node}`,platform:plan.runtime.platform,architecture:plan.runtime.architecture,python:`Python ${plan.runtime.python}`} };
  report.commands = fastCommands(plan).map(([name,command,args]) => ({name,command,args,exit_code:0}));
  for (const layer of ['frontend','backend','browser']) {
    const selected = layer === 'browser' ? plan.browser : plan[layer];
    if (!selected.length) continue;
    const expected = selected.map(value => layer === 'browser' ? {id:testIdentity(value),identity:testIdentity(value)} : {id:`${value}::example`,file:value});
    report[layer] = {expected,tests:expected.map(value => ({...value,status:'passed',retries:0}))};
  }
  if (plan.tier === 'STUDY') {
    report.studyStages = postgresTestStages({mode:'study',nativeChecks:plan.nativeChecks});
    report.scenarios = {mode:'study',commit:plan.head,plan_hash:plan.hash,planned_stages:report.studyStages,
      stages:Object.fromEntries(report.studyStages.map(stage => [stage,{exit_code:0}]))};
  }
  if (plan.nativeChecks?.length) report.native = {head:plan.head,planHash:plan.hash,
    runId:execution.id,attempt:execution.attempt,repository:plan.repository,
    checks:Object.fromEntries(plan.nativeChecks.map(check => [check,{expected:plan.nativeCases[check],
      tests:plan.nativeCases[check].map(id => ({id,status:'passed',retries:0}))}]))};
  return {plan,report,needs:{selection:{result:'success'},fast:{result:'success'}}};
}
function completePlan() {
  const plan = {version:3,event:'pull_request',tier:'qualification',scope:'complete',...context,commit:pr.merge_commit_sha};
  return {...plan,hash:planHash(plan)};
}
const failures = fixture => validateFastGate(fixture.plan,fixture.report,fixture.needs,pr,execution);

test('issue239_cleanup_regression_is_FAST_and_omits_unrelated_tests', () => {
  const selection = selectFastLane(['tests/unit/redis-admission-cleanup-regressions.test.ts']);
  assert.equal(selection.tier, 'FAST');
  assert.deepEqual(selection.frontend, ['tests/unit/redis-admission-cleanup-regressions.test.ts']);
  assert.deepEqual(selection.backend, []);
  assert.deepEqual(selection.browser, []);
});
test('issue239_javascript_fixture_consumers_invalidate_dependency_certificate', () => {
  const consumerPath='tests/issue239-temporary-consumer.js';
  let consumerCreated=false;
  try {
    writeFileSync(consumerPath, 'import "./browser/support/redis-admission-cleanup";\n', {flag:'wx'});
    consumerCreated=true;
    assert.equal(selectFastLane(['tests/unit/redis-admission-cleanup-regressions.test.ts']).tier,'FULL');
  } finally { if (consumerCreated) rmSync(consumerPath); }
});
test('issue239_fixture_selects_native_proof_and_owning_browser_without_FULL', () => {
  const selection = selectFastLane(['database/tests/check_postgres_redis_admission.py']);
  assert.equal(selection.tier, 'STUDY');
  assert.deepEqual(selection.nativeChecks, ['redis-fixture-isolation']);
  assert(selection.backend.includes('backend/tests/test_redis_admission_fixture.py'));
  assert(selection.browser.some(value => value.file === 'redis-admission-recovery.spec.ts'));
  assert.equal(selection.browser.length, fastInventory.studyBrowser.length + 1);
  assert(!selection.backend.includes('backend/tests/test_game_sync.py'));
});
test('issue239_shared_and_unknown_changes_expand_certified_selection', () => {
  for (const path of ['database/tests/test-postgres-docker.mjs', 'scripts/ci-fast-lane-inventory.json',
    'backend/app/main.py', 'tests/browser/ui-fixtures.ts', 'scripts/unknown-fixture.py', 'backend/tests/test_ci_native_unittest.py']) {
    assert.equal(selectFastLane(['database/tests/check_postgres_redis_admission.py', path]).tier, 'FULL', path);
  }
});
test('issue239_new_callers_imports_dynamic_dependencies_and_browser_cases_fail_closed', () => {
  const sources = readCertificateSources();
  const candidate = 'database/tests/check_postgres_redis_admission.py';
  for (const changed of [
    {...sources,'backend/app/new_consumer.py':'import check_postgres_redis_admission'},
    {...sources,[candidate]:sources[candidate]+'\nimport new_dependency\n'},
    {...sources,[candidate]:sources[candidate]+'\n__import__(dependency_name)\n'},
    {...sources,[candidate]:sources[candidate]+'\nquery = "SELECT * FROM unknown_shared_table"\n'},
    {...sources,[candidate]:sources[candidate]+'\nquery = "update unknown_shared_table set value=1"\n'},
    {...sources,[candidate]:sources[candidate]+"\nrelation_name = 'unknown_shared_table'\n"},
    {...sources,'tests/browser/redis-admission-recovery.spec.ts':sources['tests/browser/redis-admission-recovery.spec.ts']+'\ntest("new case", async () => {});\n'},
    {...sources,'database/tests/check_postgres_redis_fixture_isolation.py':sources['database/tests/check_postgres_redis_fixture_isolation.py']+'\nclass UnexpectedFixture(unittest.TestCase):\n    pass\n'},
    {...sources,[candidate]:undefined},
  ]) assert.equal(selectFastLane([candidate],{certificateSources:changed}).tier,'FULL');
  assert(Object.keys(certificate.dependencies).includes(candidate));
});
test('issue239_mixed_certified_changes_union_evidence_and_complete_boundaries_stay_FULL', () => {
  const selection = selectFastLane(['tests/browser/support/redis-admission-cleanup.ts','backend/tests/test_redis_admission_fixture.py']);
  assert.equal(selection.tier,'STUDY');
  assert.equal(selection.frontend.length,1); assert.equal(selection.backend.length,1);
  assert.equal(selection.browser.length,11); assert.deepEqual(selection.nativeChecks,['redis-fixture-isolation']);
  assert.equal(selectFastLane(selection.paths,{complete:true}).tier,'FULL');
  assert.equal(selectFastLane(selection.paths,{comparisonAvailable:false}).tier,'FULL');
  assert.equal(selectFastLane(changedPathsFromNameStatus('R100\0database/tests/check_postgres_redis_admission.py\0scripts/new-fixture.py\0')).tier,'FULL');
  const inventory=structuredClone(fastInventory);
  inventory.certifiedTests[0].frontend=['tests/unit/missing.test.ts'];
  assert.equal(selectFastLane(['tests/unit/redis-admission-cleanup-regressions.test.ts'],{inventory}).tier,'FULL');
  inventory.certifiedTests.push(inventory.certifiedTests[0]);
  assert.equal(selectFastLane(['tests/unit/redis-admission-cleanup-regressions.test.ts'],{inventory}).tier,'FULL');
});
test('issue239_native_and_added_browser_evidence_is_mandatory_and_revision_bound', () => {
  const candidate='database/tests/check_postgres_redis_admission.py';
  assert.deepEqual(failures(fixture(candidate)),[]);
  for (const mutate of [
    value => delete value.report.native,
    value => value.report.native.head='c'.repeat(40),
    value => value.report.native.planHash='wrong',
    value => value.report.native.runId++,
    value => value.report.native.attempt++,
    value => value.report.native.checks['unexpected']={},
    value => value.report.native.checks['redis-fixture-isolation'].tests=[],
    value => { value.report.native.checks['redis-fixture-isolation'].tests.pop(); value.report.native.checks['redis-fixture-isolation'].expected=value.report.native.checks['redis-fixture-isolation'].expected.slice(0,-1); },
    value => value.report.native.checks['redis-fixture-isolation'].tests[0].status='skipped',
    value => value.report.native.checks['redis-fixture-isolation'].tests[0].status='failed',
    value => value.report.native.checks['redis-fixture-isolation'].tests[0].retries=1,
    value => value.report.native.checks['redis-fixture-isolation'].tests.push(value.report.native.checks['redis-fixture-isolation'].tests[0]),
    value => value.report.browser.tests.pop(),
    value => delete value.report.scenarios.stages['redis-fixture-isolation'],
    value => value.report.commands.pop(),
  ]) {const evidence=fixture(candidate);mutate(evidence);assert(failures(evidence).length);}
});
test('issue239_native_runner_option_is_allowlisted_bounded_and_before_browser', () => {
  const options=parsePostgresTestOptions(['--mode','study','--browser-grep','case','--native-check','redis-fixture-isolation'],{});
  const stages=postgresTestStages(options);
  assert(stages.indexOf('redis-fixture-isolation')<stages.indexOf('browser'));
  assert.equal(stages.at(-1),'cleanup');
  assert.throws(()=>parsePostgresTestOptions(['--native-check','arbitrary'],{}));
  assert.throws(()=>parsePostgresTestOptions(['--mode','durability','--native-check','redis-fixture-isolation'],{}));
  assert.throws(()=>parsePostgresTestOptions(['--mode','browser','--native-check','redis-fixture-isolation','--native-check','redis-fixture-isolation'],{}));
});

test('emergency_docs_only_changes_do_not_trigger_full_qualification', () => {
  const selection = selectFastLane(['docs/testing.md','docs/database-security.md','.github/workflows/README.md','CONTRIBUTING.md']);
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
test('emergency_database_migration_selects_FULL', () => assert.equal(selectFastLane(['database/tempo_legacy_database/ddl/migrations/999_emergency.sql']).tier,'FULL'));
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
  const paths = changedPathsFromNameStatus('R100\0database/tempo_legacy_database/ddl/migrations/999_old.sql\0docs/new.md\0D\0scripts/ci-quality.mjs\0');
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
  assert.deepEqual(validateFastGate(plan,null,{selection:{result:'success'},quality:{result:'success'}},pr,execution,completePlan()),[]);
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
  const backend = fixture('backend/app/services/lichess_client.py'); assert.deepEqual(failures(backend),[]);
  backend.report.runtime.python = 'Python 3.11.0'; assert(failures(backend).length);
  const late = fixture(); assert(validateFastGate(late.plan,late.report,late.needs,{...pr,labels:[{name:'ci:full'}]},execution).length);
});

test('emergency_inactive_provider_adapter_selects_FAST_without_durability', () => {
  const selection = selectFastLane(['backend/app/services/lichess_client.py']);
  assert.equal(selection.tier,'FAST'); assert(selection.backend.includes('backend/tests/test_game_sync.py'));
  assert.equal(selection.browser.length,0); assert.equal(selection.python,true);
});

test('emergency_FULL_base_advancement_requires_fresh_integration', () => {
  const plan = makeFastPlan(['database/tempo_legacy_database/ddl/migrations/999_new.sql'],context);
  assert(validateFastGate(plan,null,{selection:{result:'success'},quality:{result:'success'}},{...pr,base:{...pr.base,sha:'c'.repeat(40)}},execution).length);
});

test('PR214_draft_candidates_never_qualify_FAST_or_STUDY', () => {
  for (const path of ['app/components/prefix-comparison-dialog.tsx','app/lib/review-outbox.ts']) {
    const evidence = fixture(path);
    assert(validateFastGate(evidence.plan,evidence.report,evidence.needs,{...pr,draft:true},execution).length);
  }
});

test('PR214_draft_ready_transitions_require_fresh_ready_selection', () => {
  for (const path of ['app/components/prefix-comparison-dialog.tsx','app/lib/review-outbox.ts','scripts/ci-quality.mjs']) {
    const evidence = fixture(path);
    evidence.needs.quality = {result:'success'};
    const full = completePlan();
    assert.deepEqual(validateFastGate(evidence.plan,evidence.report,evidence.needs,pr,execution,full),[]);
    assert(validateFastGate(evidence.plan,evidence.report,evidence.needs,{...pr,draft:true},execution,full).length);
    const draftPlan = {...evidence.plan,draft:true}; draftPlan.hash = planHash(draftPlan);
    assert(validateFastGate(draftPlan,evidence.report,evidence.needs,pr,execution,full).length);
    for (const draft of [undefined,null]) {
      const incomplete = {...evidence.plan,draft}; incomplete.hash = planHash(incomplete);
      assert(validateFastGate(incomplete,evidence.report,evidence.needs,pr,execution,full).length);
      assert(validateFastGate(evidence.plan,evidence.report,evidence.needs,{...pr,draft},execution,full).length);
    }
  }
});
test('PR214_FULL_gate_binds_qualified_plan_to_current_integration', () => {
  const selection = makeFastPlan(['scripts/ci-quality.mjs'],context);
  const needs = {selection:{result:'success'},quality:{result:'success'}};
  const full = completePlan();
  assert.deepEqual(validateFastGate(selection,null,needs,pr,execution,full),[]);
  assert(validateFastGate(selection,null,needs,pr,execution).length);
  for (const mutation of [{commit:'wrong'},{base:'wrong'},{head:'wrong'},{repository:'other/repo'},
    {repositoryId:2},{pullRequest:1},{draft:true},{tier:'development'},{scope:'targeted'},{event:'push'},{version:2}]) {
    const altered = {...full,...mutation}; altered.hash = planHash(altered);
    assert(validateFastGate(selection,null,needs,pr,execution,altered).length,JSON.stringify(mutation));
  }
  assert(validateFastGate(selection,null,needs,pr,execution,{...full,hash:'invalid'}).length);
  for (const mutation of [{merge_commit_sha:'new-integration'},{merge_commit_sha:null},
    {head:{sha:'new-head'}},{base:{...pr.base,sha:'new-base'}}]) {
    assert(validateFastGate(selection,null,needs,{...pr,...mutation},execution,full).length);
  }
  for (const result of [undefined,'failure','cancelled','skipped']) {
    assert(validateFastGate(selection,null,{...needs,quality:{result}},pr,execution,full).length);
  }
});
test('PR214_sensitive_prose_is_FAST_and_executable_security_is_FULL', () => {
  for (const path of ['docs/database-security.md','docs/authentication.md','.github/workflows/README.md']) {
    const selection = selectFastLane([path]); assert.equal(selection.tier,'FAST');
    assert.deepEqual(fastCommands(makeFastPlan([path],context)).map(([name])=>name),['conflicts','migrations','diff']);
  }
  for (const path of ['backend/app/auth.py','backend/app/security.py','scripts/ci-fast-lane.mjs',
    'scripts/ci-merge-gate.mjs','.github/workflows/pages.yml','scripts/ci-fast-lane-inventory.json']) {
    assert.equal(selectFastLane([path]).tier,'FULL');
  }
});
test('PR214_FULL_workflow_downloads_same_run_plan_and_retains_development_routing', () => {
  const workflow = readFileSync('.github/workflows/pages.yml','utf8');
  const gate = workflow.split('  merge-gate:')[1].split('  plan:')[0];
  assert(gate.includes('needs: [selection, fast, quality]'));
  assert(gate.includes("if: needs.selection.outputs.tier == 'FULL'"));
  assert(gate.includes('name: ci-plan'));
  assert(gate.includes('path: test-results/ci'));
  assert(!gate.includes('run-id:'));
  assert(workflow.includes("if: always() && needs.plan.result == 'success' && (needs.plan.outputs.tier"));
});

const reviewedInstructionPaths = [
  ".github/skills/domain-model-alignment-review/SKILL.md",
  "backend/ORGANIZATION.md",
  "database/PERSISTENCE-DISPOSITION.md",
  "database/SIMPLIFICATION.md"
];
test('issue543_exact_instruction_paths_are_FAST_without_runtime_work', () => {
  for (const path of reviewedInstructionPaths) {
    const selected = selectFastLane([path]);
    assert.equal(selected.tier, 'FAST', path);
    assert.equal(selected.node, false, path); assert.equal(selected.python, false, path);
    for (const key of ['frontend', 'backend', 'browser', 'nativeChecks']) assert.deepEqual(selected[key], [], `${path}: ${key}`);
    assert.deepEqual(fastCommands(makeFastPlan([path], context)).map(([name]) => name), ['conflicts', 'migrations', 'diff']);
  }
});
test('issue543_complete_24_file_instruction_repair_stays_on_existing_prose_path', () => {
  const paths = [
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/testing.md",
    "docs/architecture/study-first-reduction-2026-10-21/SMALL-TASKS.md",
    ".github/skills/domain-model-alignment-review/SKILL.md",
    ".github/skills/domain-model-alignment-review/README.md",
    "docs/architecture/component-reconstruction.md",
    "docs/architecture/diagrams/README.md",
    "docs/UI-CONTRACT.md",
    "README.md",
    "backend/ORGANIZATION.md",
    "database/README.md",
    "database/PERSISTENCE-DISPOSITION.md",
    "database/SIMPLIFICATION.md",
    "docs/architecture/study-first-reduction-2026-10-21/README.md",
    "docs/architecture/study-first-reduction-2026-10-21/PLAN.md",
    "docs/architecture/study-first-reduction-2026-10-21/DECISION.md",
    "docs/architecture/study-first-reduction-2026-10-21/FEATURES.md",
    "docs/architecture/study-first-reduction-2026-10-21/LAUNCHER-CUTOVER.md",
    "docs/architecture/ci-isolation/audit-report.md",
    "docs/architecture/ci-isolation/issue-specs.md",
    "docs/architecture/ci-isolation/parallel-impl.md",
    "docs/architecture/ci-classification-audit/report.md",
    "tests/browser/README.md"
];
  const selected = selectFastLane(paths);
  assert.equal(paths.length, 24); assert.equal(selected.tier, 'FAST');
  assert.equal(selected.node, false); assert.equal(selected.python, false);
  for (const key of ['frontend', 'backend', 'browser', 'nativeChecks']) assert.deepEqual(selected[key], [], key);
  assert.deepEqual(fastCommands(makeFastPlan(paths, context)).map(([name]) => name), ['conflicts', 'migrations', 'diff']);
});
test('issue543_prose_companions_do_not_downgrade_sensitive_unknown_or_invalid_paths', () => {
  const sensitive = [
    '.github/workflows/pages.yml', '.github/skills/domain-model-alignment-review/helper.mjs',
    '.github/skills/another-skill/SKILL.md', 'database/other-proposal.md', 'backend/UNKNOWN.md',
    'database/tempo_legacy_database/ddl/migrations/999_instruction.sql',
    'database/tempo_legacy_database/adapters/study/game_feedback.py',
    'scripts/ci-fast-lane.mjs', 'scripts/ci-fast-lane-inventory.json',
    'tests/runner/ci-fast-lane.test.mjs', 'tests/browser/ui-fixtures.ts',
    'package.json', 'package-lock.json', 'backend/app/main.py', 'app/unknown-source.ts',
    '/backend/ORGANIZATION.md', 'backend/../backend/ORGANIZATION.md',
  ];
  for (const path of sensitive) {
    assert.equal(selectFastLane([path]).tier, 'FULL', path);
    assert.equal(selectFastLane([...reviewedInstructionPaths, path]).tier, 'FULL', path);
  }
  const current = selectFastLane(['app/lib/review-outbox.ts']);
  const combined = selectFastLane([...reviewedInstructionPaths, 'app/lib/review-outbox.ts']);
  assert.equal(combined.tier, 'STUDY');
  for (const key of ['frontend', 'backend', 'browser', 'nativeChecks', 'node', 'python']) assert.deepEqual(combined[key], current[key], key);
});
test('issue543_renames_and_deletions_keep_the_sensitive_side_of_reviewed_prose', () => {
  for (const changes of [
    'R100\0scripts/ci-fast-lane.mjs\0backend/ORGANIZATION.md\0',
    'R100\0.github/skills/domain-model-alignment-review/SKILL.md\0.github/skills/domain-model-alignment-review/helper.mjs\0',
    'D\0database/tempo_legacy_database/adapters/study/game_feedback.py\0M\0database/SIMPLIFICATION.md\0',
  ]) assert.equal(selectFastLane(changedPathsFromNameStatus(changes)).tier, 'FULL');
});
test('issue543_complete_comparison_and_late_full_requirements_still_apply_to_prose', () => {
  for (const options of [{complete: true}, {comparisonAvailable: false}]) {
    assert.equal(selectFastLane(reviewedInstructionPaths, options).tier, 'FULL');
  }
  const prose = fixture(reviewedInstructionPaths[0]);
  assert.deepEqual(failures(prose), []);
  assert(validateFastGate(prose.plan, prose.report, prose.needs, {...pr, labels: [{name: 'ci:full'}]}, execution).length);
});

test('spec_prose_exact_root_path_uses_only_existing_static_FAST_commands', () => {
  for (const paths of [['SPEC.md'], ['SPEC.md', ...reviewedInstructionPaths]]) {
    const selected = selectFastLane(paths);
    assert.equal(selected.tier, 'FAST');
    assert.equal(selected.node, false); assert.equal(selected.python, false);
    for (const key of ['frontend', 'backend', 'browser', 'nativeChecks']) assert.deepEqual(selected[key], [], key);
    assert.deepEqual(fastCommands(makeFastPlan(paths, context)), [
      ['conflicts', 'node', ['scripts/check-merge-conflicts.mjs']],
      ['migrations', 'node', ['database/tooling/check-migration-inventory.mjs']],
      ['diff', 'git', ['diff', '--check', `${context.base}...${context.head}`]],
    ]);
  }
});
test('spec_prose_sensitive_unknown_and_dependency_companions_remain_FULL', () => {
  for (const path of [
    'database/tempo_legacy_database/ddl/migrations/999_spec.sql',
    '.github/workflows/pages.yml', 'scripts/ci-fast-lane.mjs',
    'scripts/ci-verification-plan.mjs', 'tests/runner/ci-fast-lane.test.mjs',
    'app/unknown-source.ts', 'package.json', 'backend/requirements.txt',
    'OTHER.md', 'backend/SPEC.md', '/SPEC.md', 'backend/../SPEC.md',
  ]) assert.equal(selectFastLane(['SPEC.md', path]).tier, 'FULL', path);
});
test('spec_prose_companion_preserves_existing_STUDY_requirements', () => {
  const path = 'app/lib/review-outbox.ts';
  const current = selectFastLane([path]);
  const combined = selectFastLane(['SPEC.md', path]);
  assert.equal(combined.tier, 'STUDY');
  for (const key of ['frontend', 'backend', 'browser', 'nativeChecks', 'node', 'python']) {
    assert.deepEqual(combined[key], current[key], key);
  }
});
test('spec_prose_renames_classify_both_sides_and_deletions_remain_sensitive', () => {
  for (const [before, after] of [
    ['scripts/ci-fast-lane.mjs', 'SPEC.md'],
    ['SPEC.md', 'scripts/ci-fast-lane.mjs'],
  ]) {
    const paths = changedPathsFromNameStatus(`R100\0${before}\0${after}\0`);
    assert.deepEqual(paths, [before, after].sort());
    assert.equal(selectFastLane(paths).tier, 'FULL');
  }
  const deleted = changedPathsFromNameStatus('D\0scripts/ci-fast-lane.mjs\0M\0SPEC.md\0');
  assert.deepEqual(deleted, ['SPEC.md', 'scripts/ci-fast-lane.mjs']);
  assert.equal(selectFastLane(deleted).tier, 'FULL');
});
test('spec_prose_complete_and_unavailable_comparison_remain_FULL', () => {
  for (const options of [{complete: true}, {comparisonAvailable: false}]) {
    assert.equal(selectFastLane(['SPEC.md'], options).tier, 'FULL');
  }
});
test('spec_prose_late_full_readiness_runtime_and_head_guards_remain_required', () => {
  const prose = fixture('SPEC.md');
  assert.deepEqual(failures(prose), []);
  for (const current of [
    {...pr, labels: [{name: 'ci:full'}]}, {...pr, draft: true},
    {...pr, draft: undefined}, {...pr, head: {sha: 'c'.repeat(40)}},
  ]) assert(validateFastGate(prose.plan, prose.report, prose.needs, current, execution).length);
  const draftPlan = {...prose.plan, draft: true}; draftPlan.hash = planHash(draftPlan);
  assert(validateFastGate(draftPlan, prose.report, prose.needs, pr, execution).length);
  for (const [key, value] of [['node', 'v0.0.0'], ['platform', 'other'], ['architecture', 'other']]) {
    const altered = fixture('SPEC.md'); altered.report.runtime[key] = value;
    assert(failures(altered).length, key);
  }
  for (const mutate of [
    value => value.report.head = 'c'.repeat(40),
    value => value.report.commands.pop(),
    value => value.report.commands[0].exit_code = 1,
    value => value.needs.fast.result = 'skipped',
  ]) { const altered = fixture('SPEC.md'); mutate(altered); assert(failures(altered).length); }
  assert.deepEqual(validateFastGate(prose.plan, prose.report, prose.needs,
    {...pr, base: {...pr.base, sha: 'c'.repeat(40)}}, execution), []);
});
test('spec_prose_selector_repair_retains_FULL_current_integration_qualification', () => {
  const plan = makeFastPlan(['SPEC.md', 'scripts/ci-fast-lane.mjs'], context);
  assert.equal(plan.tier, 'FULL');
  const needs = {selection: {result: 'success'}, quality: {result: 'success'}};
  assert.deepEqual(validateFastGate(plan, null, needs, pr, execution, completePlan()), []);
  assert(validateFastGate(plan, null, needs, pr, execution).length);
  assert(validateFastGate(plan, null, needs,
    {...pr, base: {...pr.base, sha: 'c'.repeat(40)}}, execution, completePlan()).length);
});
