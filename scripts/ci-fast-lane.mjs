import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, appendFileSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { fastCommands } from './ci-fast-commands.mjs';
import { postgresTestStages } from './postgres-test-plan.mjs';
import { changedPathsFromNameStatus, planHash, runtime } from './ci-verification-plan.mjs';

export const fastInventory = JSON.parse(readFileSync(new URL('./ci-fast-lane-inventory.json', import.meta.url), 'utf8'));
const sorted = values => [...new Set(values)].sort();
export const testIdentity = test => `${test.project}:${test.file}:${test.title}`;
export function selectFastLane(paths, { comparisonAvailable = true, complete = false, inventory = fastInventory } = {}) {
  let tier = 'FAST';
  const frontend = [], backend = [], reasons = [];
  const full = reason => { tier = 'FULL'; reasons.push(reason); };
  if (complete || !comparisonAvailable) full(complete ? 'Complete qualification requested' : 'Comparison unavailable');
  for (const path of paths) {
    if (!path || path.startsWith('/') || path.split('/').includes('..')) { full('Invalid path'); continue; }
    const mappings = inventory.sources.filter(mapping => mapping.paths.includes(path));
    if (inventory.fullPaths.includes(path) || inventory.fullPrefixes.some(prefix => path.startsWith(prefix))
      || /(?:auth|security|database|postgres_store|postgres_readiness|command_executor|redis_admission_gate)/i.test(path)
      || /^docker|\.ya?ml$|\.lock$/.test(path)) { full(`${path}: global invariant or qualification infrastructure`); continue; }
    if ((/^docs\/.+\.md$/.test(path) || /(?:^|\/)README\.md$/.test(path)
      || ['AGENTS.md','CONTRIBUTING.md','tests/REGRESSIONS.md','THIRD_PARTY_NOTICES.md'].includes(path))) {
      reasons.push(`${path}: prose`); continue;
    }
    if (mappings.length === 1) {
      const mapping = mappings[0];
      if (!['FAST','STUDY'].includes(mapping.tier) || !mapping.frontend?.length && !mapping.backend?.length) { full(`${path}: invalid ownership`); continue; }
      if (mapping.tier === 'STUDY' && tier !== 'FULL') tier = 'STUDY';
      frontend.push(...mapping.frontend); backend.push(...mapping.backend);
      reasons.push(`${path}: reviewed ${mapping.tier} test ownership`);
    } else if (/^tests\/unit\/.*\.test\.tsx?$/.test(path) || /^backend\/tests\/.*test_[^/]+\.py$/.test(path)) {
      if (!existsSync(path)) { full(`${path}: removed test ownership`); continue; }
      const studyTest = /study|train|review|receipt|schedul|tactic|admission|queue|opening|fsrs|quota/i.test(path);
      if (studyTest && tier !== 'FULL') tier = 'STUDY';
      (path.startsWith('tests/') ? frontend : backend).push(path);
      reasons.push(`${path}: changed regression executes directly`);
    } else full(`${path}: unknown or ambiguous ownership`);
  }
  if ([...frontend,...backend].some(file => !existsSync(file))) full('Selected test file missing');
  const browser = tier === 'STUDY' ? inventory.studyBrowser : [];
  if (tier === 'STUDY' && (!browser.length || new Set(browser.map(testIdentity)).size !== browser.length)) full('Invalid study smoke inventory');
  const node = frontend.length > 0 || tier === 'STUDY';
  const python = backend.length > 0 || frontend.some(file => existsSync(file) && /python|backend\/|resolvePython|scripts\//i.test(readFileSync(file,'utf8')));
  return { tier, paths: sorted(paths), frontend: sorted(frontend), backend: sorted(backend), browser: tier === 'FULL' ? [] : browser,
    node, python, reasons, inventoryHash: createHash('sha256').update(JSON.stringify(inventory)).digest('hex') };
}

export function makeFastPlan(paths, context, options = {}) {
  const selection = selectFastLane(paths, options);
  const plan = { version: 1, ...selection, ...context, runtime };
  return { ...plan, hash: planHash(plan) };
}

export function validateFastGate(plan, report, needs, currentPullRequest, execution) {
  const failures = [];
  if (!plan || plan.version !== 1 || plan.hash !== planHash(plan) || needs.selection?.result !== 'success') return ['Missing or invalid selection plan'];
  if (!['FAST','STUDY','FULL'].includes(plan.tier)) failures.push('Unknown qualification tier');
  if (!currentPullRequest || currentPullRequest.number !== plan.pullRequest || currentPullRequest.state !== 'open'
    || currentPullRequest.head?.sha !== plan.head || currentPullRequest.base?.repo?.id !== plan.repositoryId
    || currentPullRequest.base.repo.full_name !== plan.repository) failures.push('PR identity or head changed');
  if (currentPullRequest?.labels?.some(label => label.name === 'ci:full') && plan.tier !== 'FULL') failures.push('Complete qualification requested after selection');
  if (plan.tier === 'FULL') {
    if (needs.quality?.result !== 'success') failures.push('Complete qualification did not pass');
    return failures;
  }
  if (needs.fast?.result !== 'success' || !report || report.status !== 'success' || !report.completed
    || report.planHash !== plan.hash || report.head !== plan.head || report.repository !== plan.repository
    || report.pullRequest !== plan.pullRequest || report.runId !== execution.id || report.attempt !== execution.attempt) return [...failures,'Missing, failed, cancelled or stale selected evidence'];
  if (report.runtime?.node !== `v${plan.runtime.node}` || report.runtime?.platform !== plan.runtime.platform || report.runtime?.architecture !== plan.runtime.architecture) failures.push('Execution runtime differs from selected runtime');
  if (!report.commands?.length || report.commands.some(command => command.exit_code !== 0 || command.error)) failures.push('Selected commands failed or did not execute');
  if (JSON.stringify(fastCommands(plan)) !== JSON.stringify(report.commands.map(({name,command,args}) => [name,command,args]))) failures.push('Missing selected command evidence');
  for (const layer of ['frontend','backend','browser']) {
    const selected = layer === 'browser' ? plan.browser : plan[layer];
    if (!selected.length) continue;
    const evidence = report[layer];
    if (!evidence?.expected?.length || !evidence.tests?.length
      || JSON.stringify(sorted(evidence.expected.map(test => test.id))) !== JSON.stringify(sorted(evidence.tests.map(test => test.id)))
      || new Set(evidence.tests.map(test => test.id)).size !== evidence.tests.length
      || evidence.tests.some(test => test.status !== 'passed' || test.retries !== 0)) {
      failures.push(`${layer}: missing, zero, skipped or failed selected tests`); continue;
    }
    if (layer === 'browser') {
      if (JSON.stringify(sorted(selected.map(testIdentity))) !== JSON.stringify(sorted(evidence.tests.map(test => test.identity)))) failures.push('Browser identities differ from selected smoke');
    } else for (const file of selected) if (!evidence.expected.some(test => test.file === file)) failures.push(`${layer}: selected file did not execute: ${file}`);
  }
  if (plan.tier === 'STUDY') {
    const scenarios = report.scenarios;
    if (!scenarios || scenarios.mode !== 'study' || scenarios.commit !== plan.head || scenarios.plan_hash !== plan.hash
      || JSON.stringify(scenarios.planned_stages) !== JSON.stringify(postgresTestStages({mode:'study'}))
      || JSON.stringify(Object.keys(scenarios.stages ?? {}).sort()) !== JSON.stringify([...postgresTestStages({mode:'study'})].sort())
      || postgresTestStages({mode:'study'}).some(stage => scenarios.stages[stage]?.exit_code !== 0)) failures.push('Study durability evidence missing or failed');
  }
  return failures;
}

function git(args) {
  const result = spawnSync('git', args, {encoding:'utf8',maxBuffer:16*1024*1024});
  if (result.status !== 0) throw new Error(`Git comparison unavailable: ${args[0]}`);
  return result.stdout.trimEnd();
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const event = JSON.parse(readFileSync(process.env.GITHUB_EVENT_PATH,'utf8'));
  const pr = event.pull_request;
  let paths = [], comparisonAvailable = false;
  if (pr) {
    try { paths = changedPathsFromNameStatus(git(['diff','--name-status','-z','--find-renames',`${pr.base.sha}...${pr.head.sha}`])); comparisonAvailable = true; }
    catch (error) { console.error(error.message); }
  }
  const complete = !pr || pr.labels?.some(label => label.name === 'ci:full');
  const plan = makeFastPlan(paths, {repository:event.repository.full_name,repositoryId:event.repository.id,
    pullRequest:pr?.number ?? null,head:pr?.head.sha ?? process.env.GITHUB_SHA,base:pr?.base.sha ?? null}, {comparisonAvailable,complete});
  mkdirSync('test-results/fast-lane',{recursive:true});
  writeFileSync('test-results/fast-lane/plan.json',JSON.stringify(plan,null,2));
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT,`tier=${plan.tier}\nnode=${plan.node}\npython=${plan.python}\nhead=${plan.head}\n`);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY,`## Emergency qualification: ${plan.tier}\n\nHead: ${plan.head}\n\n${plan.reasons.map(reason => `- ${reason}`).join('\n')}\n\nSelected unit files: ${plan.frontend.length}; backend files: ${plan.backend.length}; browser cases: ${plan.browser.length}.\n`);
}
