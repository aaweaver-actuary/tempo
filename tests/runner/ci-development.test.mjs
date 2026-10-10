import { attachPassingBrowserShards } from "./ci-browser-shard-fixture.mjs";
import { suiteFingerprint } from "../../scripts/ci-evidence.mjs";
import test from 'node:test';
import assert from 'node:assert/strict';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, relative } from 'node:path';
import { verificationPlan, inventory } from '../../scripts/ci-verification-plan.mjs';
import { evaluateQuality, layerFailures } from '../../scripts/ci-quality.mjs';
function temporaryContractDirectory(prefix, parent = join(process.cwd(),'test-results')) {
  mkdirSync(parent,{recursive:true});
  return mkdtempSync(join(parent,prefix));
}
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

import { verificationContext, planHash, allLayers, createPlan } from '../../scripts/ci-verification-plan.mjs';
import { layerCommands, validateUnitResults, executeLayer, backendUnitFileCounts, frontendInventory, frontendResults } from '../../scripts/ci-run-layer.mjs';
import { validateMigrationInventory } from '../../scripts/check-migration-inventory.mjs';
const pr = {number:132, draft: true, state: 'open', head: {sha: 'head'}, base: {sha: 'base',repo:{id:1371942142,full_name:'aaweaver-actuary/tempo'}}, labels: [], merge_commit_sha: 'integration' };
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
  for (const [layer, report] of Object.entries(reports)) {
    report.version = 2;
    report.executionKey = suiteFingerprint(planned, layer, layerCommands(layer, planned));
    report.execution = {kind:"executed",runId:100,attempt:1};
    report.environment = {node:`v${planned.runtime.node}`,platform:planned.runtime.platform,architecture:planned.runtime.architecture,
      python:`Python ${planned.runtime.python}`,rust:`rustc ${planned.runtime.rust} (fixture)`,wasmPack:`wasm-pack ${planned.runtime.wasmPack}`};
    if (layer === "browser") attachPassingBrowserShards(planned, report);
    if (["frontend","backend"].includes(layer)) {
      const requiredFiles = [...new Set([...(Array.isArray(planned.core[layer]) ? planned.core[layer] : []), ...(planned.regressionFiles[layer] ?? [])])];
      const files = requiredFiles.length ? requiredFiles : [layer === "frontend" ? "tests/unit/example.test.ts" : "backend/tests/test_example.py"];
      report.expectedTests = Array.from({length:10},(_,index)=>({id:`${files[index % files.length]}::case-${index}`,file:files[index % files.length]}));
      report.tests = report.expectedTests.map(test=>({...test,status:"passed",retries:0}));
    }
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

test('unclassified frontend source expands draft browser and pinned rendering boundaries', () => {
  const planned = captured(pr, ['app/lib/unclassified-shared-source.ts']);
  assert.equal(planned.core.frontend, 'all');
  for (const layer of ['frontend', 'build', 'browser', 'visual']) assert(planned.jobs[layer].required, layer);
  assert.deepEqual(planned.families, Object.keys(inventory.families).filter(family => family !== 'pinned').sort());
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
    assert.throws(() => validateUnitResults(layer,planned,{...evidence.reports[layer],files:{}}),/did not execute/);
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
    const planned=captured(ready), evidence=results(planned); modify(planned); planned.hash=planHash(planned);
    assert.equal(verdict(planned,evidence,ready).success,false);
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
  const report = {test_count: 1, files: backendUnitFileCounts(unrelated, [file]), expectedTests:[{id:'unrelated'}],tests:[{id:'unrelated',status:'passed',retries:0}]};
  assert.throws(() => validateUnitResults('backend', {core: {backend: 'all'}, regressionFiles: {backend: [file]}}, report), /did not execute/);
});

test('unregistered regression files cannot qualify through unrelated whole-suite passes', () => {
  const directory=mkdtempSync(join(process.cwd(),'tests','unregistered-ci-'));
  try {
    for (const filename of ['new-regression.test.ts','test_new_regression.py']) {
      const file=relative(process.cwd(),join(directory,filename));
      writeFileSync(file,'\n');
      for (const state of [pr,{...pr,draft:false}])
        assert.throws(()=>captured(state,[file]),/outside the regular test collection/);
    }
  } finally { rmSync(directory,{recursive:true,force:true}); }
});

test('PR132 reviewed provider clients select consumer tests without persistence work', () => {
  for (const path of ['backend/app/services/chesscom_client.py', 'backend/app/services/lichess_client.py']) {
    const selected = captured(pr, [path]);
    assert.deepEqual(selected.core.backend, ['backend/tests/test_game_sync.py', 'backend/tests/test_postgres_game_sync_windows.py']);
    assert.equal(selected.jobs.postgres.applicable, false);
    assert.equal(selected.jobs.frontend.applicable, false);
    assert.deepEqual(selected.families, ['defense', 'games']);
    assert(selected.jobs.browser.required);
    const mixed = captured(pr, [path, 'backend/app/services/postgres_game_sync_windows.py']);
    assert(mixed.jobs.postgres.required);
    assert.equal(mixed.core.backend, 'all');
  }
});

test('PR132 collected core inventory rejects filtered successful results', () => {
  const report = {test_count:1, files:{'tests/unit/example.test.ts':1},
    expectedTests:[{id:'one',file:'tests/unit/example.test.ts'},{id:'two',file:'tests/unit/example.test.ts'}],
    tests:[{id:'one',file:'tests/unit/example.test.ts',status:'passed',retries:0}]};
  assert.throws(() => validateUnitResults('frontend', {core:{frontend:'all'}}, report), /inventory/);
});

test('PR132 plan records repository PR inventory and runtime identity', () => {
  const selected = draft(['docs/testing.md'], {repository:'aaweaver-actuary/tempo',repositoryId:1371942142,pullRequest:132});
  assert.equal(selected.version,3);
  assert.equal(selected.repository,'aaweaver-actuary/tempo');
  assert.equal(selected.pullRequest,132);
  assert.match(selected.inventoryRevision,/^[a-f0-9]{64}$/);
  assert.equal(selected.runtime.runner,'ubuntu-24.04-arm');
});

test('PR132 suite reuse fingerprint ignores orchestration but rejects candidate suite and runtime changes', async () => {
  const {suiteFingerprint} = await import('../../scripts/ci-evidence.mjs');
  const selected = captured({...pr,draft:false}, ['docs/testing.md']);
  const commands = layerCommands('frontend', selected);
  const key = suiteFingerprint(selected,'frontend',commands);
  assert.equal(suiteFingerprint({...selected,draft:true,event:'pull_request'},'frontend',commands),key);
  for (const change of [{head:'other'}, {base:'other'}, {commit:'other'}, {repository:'other/repository'},
    {pullRequest:133}, {inventoryRevision:'other'}, {runtime:{...selected.runtime,node:'other'}}])
    assert.notEqual(suiteFingerprint({...selected,...change},'frontend',commands),key);
  assert.notEqual(suiteFingerprint(selected,'frontend',[...commands,['extra','node',[]]]),key);
});

function reusableFixture(layer = 'frontend') {
  const planned = captured({...pr,draft:false}, ['docs/testing.md']);
  const original = results(planned).reports[layer];
  const run = {id:100,status:'completed',conclusion:'success',event:'pull_request',path:'.github/workflows/pages.yml',
    repository:{id:planned.repositoryId,full_name:planned.repository},head_sha:planned.head,pull_requests:[{number:132}]};
  const job = {id:200,run_id:100,run_attempt:1,name:`${layer} / verify`,status:'completed',conclusion:'success',labels:[planned.runtime.runner],
    steps:[{name:'Run isolated verification layer',status:'completed',conclusion:'success'}]};
  const artifact = {id:300,name:`ci-result-${layer}`,expired:false,digest:`sha256:${'a'.repeat(64)}`,
    workflow_run:{id:100,repository_id:planned.repositoryId,head_sha:planned.head}};
  const receipt = {version:2,layer,planHash:planned.hash,commit:planned.commit,status:'success',completed:true,
    executionKey:original.executionKey,execution:{kind:'reused'},source:{plan:planned,report:original,runId:100,jobId:200,artifactId:300,digest:artifact.digest}};
  return {planned,receipt,metadata:{run,job,artifact,latestJobId:job.id}};
}

test('PR132 reuse binds successful execution jobs artifacts and current aggregation', async () => {
  const {validateReuseEvidence} = await import('../../scripts/ci-evidence.mjs');
  const {planned,receipt,metadata} = reusableFixture();
  const commands = layerCommands('frontend',planned);
  assert(validateReuseEvidence(planned,'frontend',receipt,commands,metadata,layerFailures));
  const evidence = results(planned); evidence.reports.frontend = receipt;
  assert.equal(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:{...pr,draft:false}}).success,false);
  assert(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:{...pr,draft:false},verifiedReuse:{frontend:metadata}}).success);
  for (const modify of [value=>{value.job.conclusion='failure';},value=>{value.job.conclusion='skipped';},value=>{value.job.conclusion='cancelled';},
    value=>{value.job.name='plan';},value=>{value.job.run_attempt=2;},value=>{value.run.status='in_progress';},value=>{value.run.conclusion='cancelled';},
    value=>{value.run.head_sha='previous';},value=>{value.run.repository.id=999;},value=>{value.artifact.expired=true;},value=>{value.artifact.digest='other';},
    value=>{value.job.steps[0].name='Test selection';},value=>{value.latestJobId=201;}]) {
    const invalid = structuredClone(metadata); modify(invalid);
    assert.throws(()=>validateReuseEvidence(planned,'frontend',receipt,commands,invalid,layerFailures));
  }
  for (const modify of [value=>{value.source.report.commands[0].exit_code=1;},value=>{value.source.report.tests.pop();},
    value=>{value.source.report.execution.kind='diagnostic';},value=>{value.source.plan.base='previous';},value=>{value.source.report.completed=false;}]) {
    const invalid = structuredClone(receipt); modify(invalid);
    assert.throws(()=>validateReuseEvidence(planned,'frontend',invalid,commands,metadata,layerFailures));
  }
});

test('PR132 artifact downloads reject expired or altered bytes', async () => {
  const {verifiedArtifactBytes} = await import('../../scripts/ci-evidence.mjs');
  const {createHash} = await import('node:crypto');
  const bytes = Buffer.from('immutable original report');
  const artifact = {digest:`sha256:${createHash('sha256').update(bytes).digest('hex')}`,expired:false};
  assert.equal(verifiedArtifactBytes(bytes,artifact),bytes);
  assert.throws(()=>verifiedArtifactBytes(Buffer.from('changed'),artifact),/digest/);
  assert.throws(()=>verifiedArtifactBytes(bytes,{...artifact,expired:true}),/expired/);
});

test('PR132 latest matching failure or missing execution cannot reuse an older pass', async () => {
  const {findReusableEvidence} = await import('../../scripts/ci-evidence.mjs');
  const {planned,receipt,metadata} = reusableFixture();
  const commands = layerCommands('frontend',planned);
  const source = {plan:planned,report:receipt.source.report,job:metadata.job,artifact:metadata.artifact};
  assert(findReusableEvidence(planned,'frontend',commands,[metadata.run],()=>source,layerFailures));
  for (const report of [{...source.report,status:'failed'},null]) {
    const newer = {...metadata.run,id:101,conclusion:'failure'};
    assert.equal(findReusableEvidence(planned,'frontend',commands,[newer,metadata.run],run=>run.id===101 ? {...source,report} : source,layerFailures),null);
  }
  assert.equal(findReusableEvidence(planned,'frontend',commands,[],()=>{throw new Error('Unexpected lookup');},layerFailures),null);
  assert.equal(findReusableEvidence(planned,'frontend',commands,[metadata.run],()=>null,layerFailures),null);
  const cancelled = {...metadata.run,conclusion:'cancelled'};
  assert.equal(findReusableEvidence(planned,'frontend',commands,[cancelled,metadata.run],()=>source,layerFailures),null);
});

test('PR132 successful reuse survives draft or sibling aggregation failure without receipt chains', async () => {
  const {findReusableEvidence} = await import('../../scripts/ci-evidence.mjs');
  const {planned,receipt,metadata} = reusableFixture();
  const commands = layerCommands('frontend',planned);
  const newer = {...metadata.run,id:101,conclusion:'failure'};
  const original = {plan:planned,report:receipt.source.report,job:metadata.job,artifact:metadata.artifact};
  const reused = {plan:planned,report:receipt,job:{...metadata.job,id:201,run_id:101},artifact:{...metadata.artifact,id:301}};
  const readRun = run => run.id === newer.id ? reused : original;
  const recovered = findReusableEvidence(planned,'frontend',commands,[newer,metadata.run],readRun,layerFailures);
  assert(recovered);
  assert.equal(recovered.source.runId,100);
  assert.equal(recovered.source.report.execution.kind,'executed');
  reused.job.conclusion='failure';
  assert.equal(findReusableEvidence(planned,'frontend',commands,[newer,metadata.run],readRun,layerFailures),null);
});

test('PR132 explicit development exclusions preserve prior exact-suite qualification for ready transitions', async () => {
  const {findReusableEvidence} = await import('../../scripts/ci-evidence.mjs');
  const {planned,receipt,metadata}=reusableFixture('postgres');
  const commands=layerCommands('postgres',planned);
  const development=captured(pr,['docs/testing.md']);
  assert.equal(development.jobs.postgres.applicable,false);
  const newer={...metadata.run,id:101,conclusion:'failure'};
  const unselected={plan:development,report:null,job:{name:'postgres',run_id:101,status:'completed',conclusion:'skipped'}};
  const original={plan:planned,report:receipt.source.report,job:metadata.job,artifact:metadata.artifact};
  const evidence=findReusableEvidence(planned,'postgres',commands,[newer,metadata.run],run=>run.id===101 ? unselected : original,layerFailures);
  assert(evidence);
  assert.equal(evidence.source.runId,100);
  unselected.job.conclusion='failure';
  assert.equal(findReusableEvidence(planned,'postgres',commands,[newer,metadata.run],run=>run.id===101 ? unselected : original,layerFailures),null);
  unselected.job.conclusion='skipped';
  // A missing result from an applicable suite still blocks fallback to the older pass.
  unselected.plan=planned;
  assert.equal(findReusableEvidence(planned,'postgres',commands,[newer,metadata.run],run=>run.id===101 ? unselected : original,layerFailures),null);
});

test('PR132 repository PR number and runtime changes invalidate quality', () => {
  const ready = {...pr,draft:false}, planned = captured(ready), evidence = results(planned);
  for (const change of [{number:133},{base:{...ready.base,repo:{id:7,full_name:'another/repo'}}}])
    assert.equal(verdict(planned,evidence,{...ready,...change}).success,false);
  for (const modify of [report=>{report.environment.node='v20.0.0';},report=>{report.execution.runId=0;},report=>{report.executionKey='previous';}]) {
    const invalid=results(planned); modify(invalid.reports.frontend);
    assert.equal(verdict(planned,invalid,ready).success,false);
  }
});

test('PR132 core inventories retain nested identities and JUnit parameter escaping', async () => {
  const {frontendInventory,frontendResults} = await import('../../scripts/ci-run-layer.mjs');
  const file = join(process.cwd(),'tests/unit/example.test.ts');
  const expected = frontendInventory([{file,name:'parent > example',location:{line:2,column:3}}]);
  const actual = frontendResults({testResults:[{name:file,assertionResults:[{ancestorTitles:['parent'],title:'example',location:{line:2,column:3},status:'passed'}]}]});
  assert.equal(actual[0].id,expected[0].id);
  assert.throws(()=>frontendInventory([{file,name:'missing location'}]),/locations/);
  const directory = temporaryContractDirectory('junit-contract-');
  try {
    const path=join(directory,'tests.xml');
    writeFileSync(path,'<testsuites><testsuite><testcase classname="backend.tests.test_example.TestCase" name="test_value[a&amp;b]"/><testcase classname="backend.tests.test_example" name="test_skipped"><skipped/></testcase></testsuite></testsuites>');
    const parsed=spawnSync(resolvePython(),['scripts/ci-collect-backend.py','--results',path],{encoding:'utf8'});
    assert.equal(parsed.status,0,parsed.stderr);
    assert.deepEqual(JSON.parse(parsed.stdout).map(item=>[item.id,item.status]),[['backend.tests.test_example.TestCase::test_value[a&b]','passed'],['backend.tests.test_example::test_skipped','skipped']]);
  } finally {rmSync(directory,{recursive:true,force:true});}
});

test('PR132 pytest collection matches actual class and parameter identities', () => {
  const directory = temporaryContractDirectory('pytest-inventory-contract-');
  try {
    const file = join(directory,'test_identities.py'), inventoryPath = join(directory,'inventory.json'), xmlPath = join(directory,'results.xml');
    writeFileSync(file,'import pytest\nclass TestIdentity:\n    @pytest.mark.parametrize("value", ["a::b&c", "second"], ids=str)\n    def test_value(self, value):\n        assert value\n');
    const argumentsForPytest = [file,'-q','--rootdir=.'];
    const collected = spawnSync(resolvePython(),['scripts/ci-collect-backend.py',inventoryPath,...argumentsForPytest],{encoding:'utf8'});
    assert.equal(collected.status,0,collected.stdout+collected.stderr);
    const executed = spawnSync(resolvePython(),['-m','pytest',...argumentsForPytest,`--junitxml=${xmlPath}`],{encoding:'utf8'});
    assert.equal(executed.status,0,executed.stdout+executed.stderr);
    const parsed = spawnSync(resolvePython(),['scripts/ci-collect-backend.py','--results',xmlPath],{encoding:'utf8'});
    assert.equal(parsed.status,0,parsed.stderr);
    const report = {test_count:2,expectedTests:JSON.parse(readFileSync(inventoryPath,'utf8')),tests:JSON.parse(parsed.stdout)};
    validateUnitResults('backend',{core:{backend:'all'}},report);
    assert(report.tests.some(item=>item.id.endsWith('TestIdentity::test_value[a::b&c]')));
  } finally {rmSync(directory,{recursive:true,force:true});}
});

test('PR132 workflow skips setup and execution only after validated reuse', () => {
  const workflow = readFileSync('.github/workflows/verify-layer.yml','utf8');
  assert(workflow.indexOf('id: evidence') < workflow.indexOf('actions/setup-python'));
  for (const setup of ['actions/setup-python','dtolnay/rust-toolchain','Swatinem/rust-cache','taiki-e/install-action','Browser prerequisites','Run isolated verification layer']) {
    const step = workflow.slice(workflow.indexOf(setup)).split(/\n      - /)[0];
    assert(step.includes("steps.evidence.outputs.reused != 'true'"),setup);
  }
  assert.match(workflow,/actions: read/);
  const source = readFileSync('scripts/ci-evidence.mjs','utf8');
  assert(source.includes('if (plan.event === "pull_request")'));
  assert(source.includes('per_page=20'));
});

test('PR132 fresh execution cannot silently substitute another workflow run', () => {
  const ready = {...pr,draft:false}, planned = captured(ready), evidence = results(planned);
  assert(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:ready,currentRun:{id:100,attempt:1}}).success);
  assert.equal(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:ready,currentRun:{id:101,attempt:1}}).success,false);
  assert(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:ready,currentRun:{id:100,attempt:2}}).success);
  evidence.reports.frontend.execution.attempt=3;
  assert.equal(evaluateQuality(planned,evidence.needs,evidence.reports,{currentPullRequest:ready,currentRun:{id:100,attempt:2}}).success,false);
});

test('PR132 evidence CLI completes imports and falls back when no reusable run exists', () => {
  const planned = captured({...pr,draft:false});
  const directory = temporaryContractDirectory('evidence-cli-contract-');
  try {
    mkdirSync(join(directory,'test-results','ci'),{recursive:true});
    writeFileSync(join(directory,'test-results','ci','plan.json'),JSON.stringify(planned));
    const gh = join(directory,'gh'), output = join(directory,'outputs');
    writeFileSync(gh,"#!/bin/sh\nprintf '%s' '{\"workflow_runs\":[]}'\n"); chmodSync(gh,0o755);
    const result = spawnSync(process.execPath,[join(process.cwd(),'scripts','ci-evidence.mjs'),'frontend'],{
      cwd:directory,encoding:'utf8',env:{...process.env,PATH:`${directory}:${process.env.PATH}`,GITHUB_REPOSITORY:planned.repository,GITHUB_OUTPUT:output}});
    assert.equal(result.status,0,result.stdout+result.stderr);
    assert.equal(readFileSync(output,'utf8'),'reused=false\n');
  } finally {rmSync(directory,{recursive:true,force:true});}
});

test('PR132 focused contract temporary outputs create an absent parent', () => {
  const fixture = mkdtempSync(join(tmpdir(),'tempo-contract-parent-'));
  try {
    const parent = join(fixture,'absent-test-results');
    assert.equal(existsSync(parent),false);
    const output = temporaryContractDirectory('proof-',parent);
    assert(existsSync(output));
    assert(output.startsWith(join(parent,'proof-')));
  } finally {rmSync(fixture,{recursive:true,force:true});}
});

test('PR132 frontend runtime inventory expands parameterized execution identities', () => {
  const file='tests/unit/attempt-lifecycle-regressions.test.ts', planned=draft([file]);
  const directory=temporaryContractDirectory('frontend-parameter-contract-');
  try {
    const inventoryPath=join(directory,'inventory.json'), resultPath=join(directory,'results.json');
    const [,command,args]=layerCommands('frontend',planned).find(([name])=>name==='unit_inventory');
    const collected=spawnSync(command,args.map(argument=>argument.startsWith('--json=') ? `--json=${inventoryPath}` : argument),{encoding:'utf8'});
    assert.equal(collected.status,0,collected.stdout+collected.stderr);
    const executed=spawnSync('npx',['vitest','run',file,'--includeTaskLocation','--reporter=json',`--outputFile=${resultPath}`],{encoding:'utf8'});
    assert.equal(executed.status,0,executed.stdout+executed.stderr);
    const unit=JSON.parse(readFileSync(resultPath,'utf8'));
    const expectedTests=frontendInventory(JSON.parse(readFileSync(inventoryPath,'utf8')));
    const observed=frontendResults(unit);
    assert(expectedTests.some(item=>item.id.includes('opponentReplyPending')));
    validateUnitResults('frontend',planned,{test_count:unit.numTotalTests,expectedTests,tests:observed,files:{[file]:unit.numTotalTests}});
  } finally {rmSync(directory,{recursive:true,force:true});}
});
