import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { verificationPlan, inventory } from '../../scripts/ci-verification-plan.mjs';
import { evaluateQuality } from '../../scripts/ci-quality.mjs';
const files = Object.values(inventory.families).flat();
const cases = inventory.critical.map((item, index) => ({ ...item, id: `case-${index}`, fullTitle: item.title, project: 'chromium' }));
function draft(paths, extra = {}) {
  return verificationPlan({ files, cases, paths, tier: 'development', draft: true, commit: 'integration', head: 'head', base: 'base', ...extra });
}
test('draft prose defaults to development evidence and never qualifies for merging', () => {
  const planned = draft(['docs/testing.md']);
  assert.equal(planned.tier, 'development');
  assert(Object.values(planned.jobs).every(job => !job.applicable));
  const needs = { plan: { result: 'success' }, ...Object.fromEntries(Object.keys(planned.jobs).map(layer => [layer, { result: 'skipped' }])) };
  assert.equal(evaluateQuality(planned, needs, {}).success, false);
});
test('changed draft regression files run directly without unrelated full layers', () => {
  const planned = draft(['tests/unit/ci-reliability-regressions.test.ts']);
  assert(planned.jobs.frontend.applicable);
  assert.equal(planned.jobs.postgres.applicable, false);
  assert(planned.core.frontend.includes('tests/unit/ci-reliability-regressions.test.ts'));
});

import { spawnSync } from 'node:child_process';
import { resolvePython } from '../../scripts/resolve-python.mjs';
test('opening admission cleanup preserves another worker lease and detects owned leaks', () => {
  const result = spawnSync(resolvePython(), ['-c', `
import ast
from pathlib import Path
source = ast.parse(Path('scripts/check_postgres_opening_evidence.py').read_text())
helper = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == '_assert_owned_background_leases_released')
exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])), '<actual fixture assertion>', 'exec'))
class Server:
    scores = {'peer': 999999}
    def zscore(self, key, token): return self.scores.get(token)
server = Server()
_assert_owned_background_leases_released(server, {'owned'}, 'background')
assert server.scores == {'peer': 999999}
server.scores['owned'] = 999999
try:
    _assert_owned_background_leases_released(server, {'owned'}, 'background')
except AssertionError as error:
    assert 'leaked its owned' in str(error)
else:
    raise AssertionError('Injected owned lease leak passed')
try:
    _assert_owned_background_leases_released(server, set(), 'background')
except AssertionError:
    pass
else:
    raise AssertionError('No observed admissions passed')
`], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});

import { verificationContext, planHash, allLayers, createPlan } from '../../scripts/ci-verification-plan.mjs';
import { layerCommands, validateUnitResults, executeLayer, backendUnitFileCounts } from '../../scripts/ci-run-layer.mjs';
import { validateMigrationInventory } from '../../scripts/check-migration-inventory.mjs';
const pr = { draft: true, state: 'open', head: {sha: 'head'}, base: {sha: 'base'}, labels: [], merge_commit_sha: 'integration' };
function captured(pullRequest = pr, paths = ['docs/testing.md']) {
  return draft(paths, verificationContext('pull_request', {pull_request: pullRequest}, false, 'integration'));
}
function results(planned) {
  const needs = {plan: {result:'success'}}, reports = {};
  for (const layer of allLayers) {
    needs[layer] = {result: planned.jobs[layer].applicable ? 'success' : 'skipped'};
    if (!planned.jobs[layer].applicable) continue;
    reports[layer] = {layer, commit:planned.commit, planHash:planned.hash, completed:true, status:'success', test_count:10,
      files: Object.fromEntries([...(Array.isArray(planned.core[layer]) ? planned.core[layer] : []), ...(planned.regressionFiles?.[layer] ?? [])].map(file => [file, 1])),
      commands: layerCommands(layer, planned).map(([name, command, args]) => ({name, command, args, exit_code:0})),
      tests: (layer === 'visual' ? planned.pinnedCollection : planned.collection.filter(item => item.selected)).map(item => ({id:item.id, status:'passed', retries:0})),
      scenarios: {runner:'postgres', mode:planned.jobs[layer].mode, commit:planned.commit, plan_hash:planned.hash,
        planned_stages:planned.jobs[layer].planned_stages, stages:Object.fromEntries((planned.jobs[layer].planned_stages ?? []).map(stage => [stage, {exit_code:0}]))}};
  }
  return {needs, reports};
}
function verdict(planned, evidence, currentPullRequest = pr, development = false) {
  return evaluateQuality(planned, evidence.needs, evidence.reports, {currentPullRequest, development});
}
test('draft ready and converted-to-draft transitions separate execution from qualification', () => {
  for (const action of ['opened','synchronize','reopened','converted_to_draft','edited','labeled','unlabeled']) {
    assert.equal(verificationContext('pull_request', {action,pull_request:pr}, false,'integration').tier, 'development');
  }
  const development = captured();
  assert(verdict(development, results(development), pr, true).success);
  assert.equal(verdict(development, results(development)).success, false);
  const ready = {...pr, draft:false};
  const qualification = captured(ready);
  for (const layer of ['frontend','backend','build','postgres','browser']) assert(qualification.jobs[layer].required);
  assert.equal(qualification.core.frontend,'all'); assert.equal(qualification.core.backend,'all');
  assert.equal(verificationContext('pull_request',{action:'ready_for_review',pull_request:ready}).tier,'qualification');
  assert(verdict(qualification, results(qualification), ready).success);
  assert.equal(verdict(qualification, results(qualification), pr).success,false);
});
test('head base integration and draft changes invalidate qualifying evidence', () => {
  const ready = {...pr,draft:false}; const planned = captured(ready);
  for (const update of [{head:{sha:'new-head'}},{base:{sha:'new-base'}},{merge_commit_sha:'new-merge'},{draft:true},{state:'closed'}]) {
    assert.equal(verdict(planned,results(planned),{...ready,...update}).success,false);
  }
  assert.equal(evaluateQuality(planned,results(planned).needs,results(planned).reports).success,false);
  for (const update of [{head:'new-head'},{base:'new-base'},{commit:'new-merge'},{draft:true}]) {
    assert.notEqual(planHash({...planned,...update}),planned.hash);
    assert.equal(verdict({...planned,...update},results(planned),ready).success,false);
  }
});
test('main merge-group nightly release and manual requests retain complete qualification', () => {
  for (const event of ['push','merge_group','schedule','release','workflow_dispatch']) {
    const context = verificationContext(event,{},false,'integration');
    assert.equal(context.tier,'qualification'); assert(context.complete);
    const planned = draft(['docs/testing.md'],context);
    for (const layer of allLayers.filter(layer => layer !== 'quarantine')) assert(planned.jobs[layer].required,layer);
  }
  for (const context of [verificationContext('pull_request',{pull_request:{...pr,labels:[{name:'ci:full'}]}},false,'integration'),
    verificationContext('pull_request',{pull_request:pr},true,'integration')]) {
    assert(context.complete); assert.equal(context.tier,'qualification');
    const planned = draft(['docs/testing.md'],context);
    assert.equal(verdict(planned,results(planned)).success,false,'Full execution on a draft must not qualify');
  }
  assert.throws(() => verificationContext('pull_request',{}),/Missing PR/);
});
test('persistence drafts retain full durability and unknown shared changes expand coverage', () => {
  const persistence = captured(pr,['backend/app/services/postgres_opening_evidence.py']);
  assert(persistence.jobs.backend.required); assert(persistence.jobs.postgres.required);
  assert.equal(persistence.jobs.postgres.mode,'durability');
  assert.equal(persistence.core.backend,'all'); assert(persistence.jobs.browser.required);
  for (const options of [{paths:['new-shared-runtime.xyz']},{paths:['backend/tests/conftest.py']},{comparisonAvailable:false}]) {
    const planned = draft(options.paths ?? [],options);
    for (const layer of allLayers.filter(layer => layer !== 'quarantine')) assert(planned.jobs[layer].required,layer);
  }
  const harness = captured(pr,['scripts/ci-run-layer.mjs']);
  assert.deepEqual(harness.core.frontend,inventory.development.harnessTests.toSorted());
  const command = layerCommands('frontend',harness).find(([name]) => name === 'unit');
  assert(harness.core.frontend.every(file => command[2].includes(file)));
});
test('changed frontend and Python regressions must execute and zero filtered skipped evidence fails', () => {
  for (const [layer,file] of [['frontend','tests/unit/ci-reliability-regressions.test.ts'],['backend','backend/tests/test_studies.py']]) {
    const planned = captured(pr,[file]); const evidence = results(planned);
    assert(verdict(planned,evidence,pr,true).success);
    for (const modify of [report => {report.test_count=0;},report => {delete report.files[file];},
      report => {report.commands[0].args.push('-t','unrelated-case');},report => {report.failed=true;}, report => {report.skipped=true;},
      report => {report.commit='stale';},report => {report.completed=false;},report => {report.commands[0].exit_code=1;}]) {
      const broken=results(planned); modify(broken.reports[layer]); assert.equal(verdict(planned,broken,pr,true).success,false);
    }
    for (const state of ['failure','cancelled','skipped',undefined]) {
      const broken=results(planned); broken.needs[layer].result=state; assert.equal(verdict(planned,broken,pr,true).success,false);
    }
    assert.throws(() => validateUnitResults(layer,planned,{test_count:1,files:{}}),/did not execute/);
    delete evidence.reports[layer]; assert.equal(verdict(planned,evidence,pr,true).success,false);
  }
});
test('diagnostic success cannot erase the original development failure', () => {
  let calls=0; const outcome=executeLayer([['unit','npm',[]]],()=>({status:++calls===1?1:0}),true);
  assert.equal(outcome.status,'failed'); assert.equal(outcome.commands[0].exit_code,1); assert.equal(outcome.diagnostics[0].exit_code,0);
});
test('migration guard rejects duplicate gaps invalid filenames and authoritative disagreement', () => {
  assert.equal(validateMigrationInventory(['001_initial.sql','002_next.sql'],'POSTGRES_SCHEMA_VERSION = 2'),2);
  for (const files of [['001_a.sql','001_b.sql'],['001_a.sql','003_c.sql'],['bad.sql'],[]]) assert.throws(()=>validateMigrationInventory(files,'POSTGRES_SCHEMA_VERSION = 2'));
  assert.throws(()=>validateMigrationInventory(['001_a.sql'],'POSTGRES_SCHEMA_VERSION = 2'),/disagrees/);
});
test('workflow subscribes to state and base events and pins every verification checkout', () => {
  const workflow=readFileSync('.github/workflows/pages.yml','utf8');
  for (const action of ['opened','synchronize','reopened','ready_for_review','converted_to_draft','edited','labeled','unlabeled']) assert(workflow.includes(action));
  assert(workflow.includes("cancel-in-progress: ${{ github.event_name == 'pull_request' }}"));
  assert(workflow.includes("  quality:\n    if: always()"));
  assert(workflow.includes('node scripts/ci-quality.mjs --development'));
  assert(workflow.includes('gh api "repos/$TEMPO_REPOSITORY/pulls/$TEMPO_PR"'));
  assert.equal((workflow.match(/candidate_sha: \$\{\{ needs.plan.outputs.commit \}\}/g)??[]).length,allLayers.length);
  assert(readFileSync('.github/workflows/verify-layer.yml','utf8').includes('ref: ${{ inputs.candidate_sha }}'));
});
test('actual planner binds checked-out revision before hashing and preserves collected inventory', () => {
  const commit=spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).stdout.trim();
  const planned=createPlan({base:commit,context:{...verificationContext('pull_request',{pull_request:pr},false,commit)}});
  assert.equal(planned.hash,planHash(planned)); assert.equal(planned.commit,commit);
  assert(planned.collection.length>6); assert.equal(planned.collection.filter(item=>item.selected).length,0);
  assert.throws(()=>createPlan({context:{commit:'different'}}),/differs/);
});
test('qualification rejects a rehashed reduced plan instead of accepting development coverage', () => {
  const ready={...pr,draft:false};
  for (const modify of [plan=>{plan.jobs.postgres={...plan.jobs.postgres,applicable:false,required:false};},
    plan=>{plan.core.frontend=['tests/unit/ci-reliability-regressions.test.ts'];},
    plan=>{plan.collection[0].selected=false;}]) {
    const planned=captured(ready); modify(planned); planned.hash=planHash(planned);
    assert.equal(verdict(planned,results(planned),ready).success,false);
  }
});
test('manual feature-branch full execution cannot qualify a draft via a head-only status', () => {
  const context=verificationContext('workflow_dispatch',{},true,'integration','refs/heads/draft-feature');
  const planned=draft(['docs/testing.md'],context);
  const evidence=results(planned);
  // A real complete plan includes pinned cases; this fixture supplies them too.
  planned.pinnedCollection=[{id:'visual',file:'visual.spec.ts'}]; planned.hash=planHash(planned);
  const completeEvidence=results(planned);
  assert.equal(evaluateQuality(planned,completeEvidence.needs,completeEvidence.reports).success,false);
  assert(evaluateQuality(planned,completeEvidence.needs,completeEvidence.reports,{development:true}).success);
  assert(evidence.needs.postgres.result==='success');
});
test('whole-subsystem qualification still requires every changed regression file', () => {
  const ready={...pr,draft:false};
  const planned=captured(ready,['app/views/studies_view.tsx','tests/unit/ci-reliability-regressions.test.ts','backend/tests/test_studies.py']);
  // Rendering is applicable; provide a pinned case in this synthetic inventory.
  planned.pinnedCollection=[{id:'visual',file:'visual.spec.ts'}]; planned.hash=planHash(planned);
  const evidence=results(planned); assert(verdict(planned,evidence,ready).success);
  for(const layer of ['frontend','backend']) {
    const missing=results(planned); missing.reports[layer].files={};
    assert.equal(verdict(planned,missing,ready).success,false);
  }
  const runner=captured(pr,['tests/runner/ci-development.test.mjs']);
  assert(runner.regressionFiles.frontend.includes('tests/unit/ci-reliability-regressions.test.ts'));
  assert.throws(()=>draft(['tests/runner/ci-development.test.mjs'],{sourceInventory:{...inventory,development:{...inventory.development,runnerOwners:{}}}}), /regular-suite ownership/);
});

test('Python regression evidence distinguishes exact modules from similarly named files', () => {
  const file = 'backend/tests/test_studies.py';
  const xml = '<testsuite tests="3"><testcase classname="backend.tests.test_studies_extra" name="unrelated"/>' +
    '<testcase classname="backend.tests.test_studies" name="function"/>' +
    '<testcase classname="backend.tests.test_studies.TestStudy" name="method"/></testsuite>';
  assert.deepEqual(backendUnitFileCounts(xml, [file]), {[file]: 2});
  const unrelated = '<testcase classname="backend.tests.test_studies_extra" name="unrelated"/>';
  const report = {test_count: 1, files: backendUnitFileCounts(unrelated, [file])};
  assert.throws(() => validateUnitResults('backend', {core: {backend: 'all'}, regressionFiles: {backend: [file]}}, report), /did not execute/);
});
