import { spawnSync } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { planHash, collectCases } from './ci-verification-plan.mjs';
import { frontendInventory, frontendResults, browserResults } from './ci-run-layer.mjs';
import { resolvePython } from './resolve-python.mjs';
import { postgresTestStages } from '../database/tests/postgres-test-plan.mjs';
import { testIdentity } from './ci-fast-lane.mjs';
import { fastCommands } from './ci-fast-commands.mjs';

export function verifySelection(expected, observed) {
  if (!expected.length || expected.length !== observed.length
    || new Set(expected.map(test => test.id)).size !== expected.length
    || JSON.stringify(expected.map(test => test.id).sort()) !== JSON.stringify(observed.map(test => test.id).sort())
    || observed.some(test => test.status !== 'passed' || test.retries !== 0)) throw new Error('Selected tests missing, skipped, failed, duplicated or empty');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const plan = JSON.parse(readFileSync('test-results/fast-lane/plan.json','utf8'));
  const report = {planHash:plan.hash,head:plan.head,repository:plan.repository,pullRequest:plan.pullRequest,
    runId:Number(process.env.GITHUB_RUN_ID),attempt:Number(process.env.GITHUB_RUN_ATTEMPT),
    runtime:{node:process.version,platform:process.platform,architecture:process.arch,
      python:plan.python ? spawnSync(resolvePython(),['--version'],{encoding:'utf8'}).stdout?.trim() : null},
    completed:false,status:'failed',commands:[]};
  mkdirSync('test-results/fast-lane',{recursive:true});
  function run(name, command, args, env = {}) {
    const start = performance.now();
    const result = spawnSync(command,args,{stdio:name === 'browser_inventory' ? ['ignore','pipe','inherit'] : 'inherit',encoding:'utf8',maxBuffer:16*1024*1024,env:{...process.env,PYTHONPATH:'backend:database',...(name === 'browser_inventory' ? {TEMPO_DOCKER_URL:'http://127.0.0.1:4173'} : {}),...env}});
    if (name === 'browser_inventory' && result.status === 0) writeFileSync('test-results/fast-lane/browser-inventory.json',result.stdout);
    report.commands.push({name,command,args,exit_code:result.status,error:result.error?.message ?? null,duration_seconds:(performance.now()-start)/1000});
    if (result.status !== 0 || result.error) throw new Error(`${name} failed`);
  }
  try {
    if (plan.hash !== planHash(plan) || spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).stdout.trim() !== plan.head) throw new Error('Plan or checkout differs from captured PR head');
    if (!['FAST','STUDY'].includes(plan.tier)) throw new Error('Complete qualification belongs to existing full infrastructure');
    report.expectedCommands = fastCommands(plan);
    for (const [name,command,args] of report.expectedCommands) {
      run(name,command,args,{TEMPO_CI_PLAN_HASH:plan.hash,
        ...(name === 'study' ? {TEMPO_CI_REPORT:'test-results/fast-lane/browser.json'} : {})});
      if (name === 'frontend') {
        const unit = JSON.parse(readFileSync('test-results/fast-lane/frontend.json','utf8'));
        report.frontend = {expected:frontendInventory(JSON.parse(readFileSync('test-results/fast-lane/frontend-inventory.json','utf8'))),tests:frontendResults(unit)};
        verifySelection(report.frontend.expected,report.frontend.tests);
      }
      if (name === 'backend') {
        const parsed = spawnSync(resolvePython(),['scripts/ci-collect-backend.py','--results','test-results/fast-lane/backend.xml'],{encoding:'utf8'});
        if (parsed.status !== 0) throw new Error('Backend results unavailable');
        report.backend = {expected:JSON.parse(readFileSync('test-results/fast-lane/backend-inventory.json','utf8')),tests:JSON.parse(parsed.stdout)};
        verifySelection(report.backend.expected,report.backend.tests);
      }
      if (name === 'browser_inventory') {
        const collection = collectCases(JSON.parse(readFileSync('test-results/fast-lane/browser-inventory.json','utf8')));
        const expected = collection.filter(test => plan.browser.some(selected => testIdentity(selected) === testIdentity(test)));
        if (expected.length !== plan.browser.length) throw new Error('Selected browser identities absent or duplicated in real collection');
        report.browser = {expected:expected.map(test => ({id:test.id,identity:testIdentity(test)})),tests:[]};
      }
      if (name === 'study') {
        const browser = JSON.parse(readFileSync('test-results/fast-lane/browser.json','utf8'));
        report.browser.tests = browserResults(browser).map(test => ({...test,identity:report.browser.expected.find(expected => expected.id === test.id)?.identity}));
        verifySelection(report.browser.expected,report.browser.tests);
        const files = readdirSync('test-results/performance').filter(file => file.startsWith('postgres-scenarios-study-'));
        if (files.length !== 1) throw new Error('Study scenario results absent or ambiguous');
        report.scenarios = JSON.parse(readFileSync(`test-results/performance/${files[0]}`,'utf8'));
        report.studyStages = postgresTestStages({mode:'study',nativeChecks:plan.nativeChecks});
        if (report.studyStages.some(stage => report.scenarios.stages?.[stage]?.exit_code !== 0)) throw new Error('Study stages failed');
        if (plan.nativeChecks?.length) {
          const nativeFiles = readdirSync('test-results/native').filter(file => file.endsWith('.json'));
          if (nativeFiles.length !== 1) throw new Error('Native fixture evidence absent or ambiguous');
          report.native = JSON.parse(readFileSync(`test-results/native/${nativeFiles[0]}`,'utf8'));
          for (const check of plan.nativeChecks) {
            const evidence = report.native.checks?.[check];
            if (!evidence) throw new Error('Native fixture evidence missing');
            verifySelection(evidence.expected.map(id => ({id})), evidence.tests);
          }
        }
      }
    }
    report.completed = true; report.status = 'success';
  } catch (error) { report.error = error.message; console.error(error); }
  finally { writeFileSync('test-results/fast-lane/result.json',JSON.stringify(report,null,2)); }
  process.exitCode = report.status === 'success' ? 0 : 1;
}
