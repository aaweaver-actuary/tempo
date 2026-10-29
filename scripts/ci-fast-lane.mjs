import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, appendFileSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { fastCommands } from './ci-fast-commands.mjs';
import { postgresTestStages } from '../database/tests/postgres-test-plan.mjs';
import { changedPathsFromNameStatus, planHash, runtime } from './ci-verification-plan.mjs';
import { certificate, readCertificateSources, validateCertificate, nativeFixtureCases } from './ci-certified-tests.mjs';

export const fastInventory = JSON.parse(readFileSync(new URL('./ci-fast-lane-inventory.json', import.meta.url), 'utf8'));
const sorted = values => [...new Set(values)].sort();
export const testIdentity = test => `${test.project}:${test.file}:${test.title}`;
export function selectFastLane(paths, { comparisonAvailable = true, complete = false, inventory = fastInventory, certificateSources } = {}) {
  let tier = 'FAST';
  const frontend = [], backend = [], reasons = [], nativeChecks = [], additionalBrowser = [];
  let certificateValid;
  const full = reason => { tier = 'FULL'; reasons.push(reason); };
  if (complete || !comparisonAvailable) full(complete ? 'Complete qualification requested' : 'Comparison unavailable');
  for (const path of paths) {
    if (!path || path.startsWith('/') || path.split('/').includes('..')) { full('Invalid path'); continue; }
    const mappings = inventory.sources.filter(mapping => mapping.paths.includes(path));
    const certifications = (inventory.certifiedTests ?? []).filter(mapping => mapping.paths.includes(path));
    if ((/^docs\/.+\.md$/.test(path) || /(?:^|\/)README\.md$/.test(path)
      || ['AGENTS.md','SPEC.md','CONTRIBUTING.md','tests/REGRESSIONS.md','THIRD_PARTY_NOTICES.md',
        'backend/app/analysis/probability/EXPLORER.md',
        '.github/skills/domain-model-alignment-review/SKILL.md','backend/ORGANIZATION.md',
        'database/PERSISTENCE-DISPOSITION.md','database/SIMPLIFICATION.md'].includes(path))) {
      reasons.push(`${path}: prose`); continue;
    }
    // Only these reviewed fixture paths may reach certificate validation below.
    // Explicit FULL paths and every other invariant/unknown path still fail closed.
    const certifiedDatabaseFixture = ['database/tests/check_postgres_redis_admission.py',
      'database/tests/check_postgres_redis_fixture_isolation.py'].includes(path);
    if (inventory.fullPaths.includes(path)
      || (!certifiedDatabaseFixture && /(?:auth|security|database|postgres_store|postgres_readiness|command_executor|redis_admission_gate)/i.test(path))
      || /^docker|\.ya?ml$|\.lock$/.test(path)) { full(`${path}: global invariant or qualification infrastructure`); continue; }
    if (certifications.length) {
      try { certificateValid ??= validateCertificate(certificate, certificateSources ?? readCertificateSources()); }
      catch { certificateValid = false; }
      const mapping = certifications[0];
      if (certifications.length !== 1 || mappings.length || !certificateValid || !existsSync(path)
        || !['FAST','STUDY'].includes(mapping.tier) || !mapping.frontend?.length
        || !Array.isArray(mapping.backend) || !Array.isArray(mapping.browser) || !Array.isArray(mapping.nativeChecks)
        || mapping.nativeChecks.some(check => check !== 'redis-fixture-isolation')) {
        full(`${path}: invalid or changed certified dependency closure`); continue;
      }
      if (mapping.tier === 'STUDY' && tier !== 'FULL') tier = 'STUDY';
      frontend.push(...mapping.frontend); backend.push(...mapping.backend);
      nativeChecks.push(...mapping.nativeChecks); additionalBrowser.push(...mapping.browser);
      reasons.push(`${path}: certified ${mapping.tier} dependency closure`);
      continue;
    }
    if (inventory.fullPrefixes.some(prefix => path.startsWith(prefix))) {
      full(`${path}: shared qualification infrastructure`); continue;
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
  const browser = tier === 'STUDY' ? [...inventory.studyBrowser, ...additionalBrowser.filter(
    (test, index) => !inventory.studyBrowser.some(value => testIdentity(value) === testIdentity(test))
      && additionalBrowser.findIndex(value => testIdentity(value) === testIdentity(test)) === index)] : [];
  if (tier === 'STUDY' && (!browser.length || new Set(browser.map(testIdentity)).size !== browser.length)) full('Invalid study smoke inventory');
  const node = frontend.length > 0 || tier === 'STUDY';
  let nativeCases = {};
  if (nativeChecks.length && tier !== 'FULL') {
    try { nativeCases = {'redis-fixture-isolation':nativeFixtureCases(
      certificateSources ? certificateSources['database/tests/check_postgres_redis_fixture_isolation.py']
        : readFileSync('database/tests/check_postgres_redis_fixture_isolation.py','utf8'))}; }
    catch { full('Unknown native fixture collection'); }
  }
  const python = backend.length > 0 || frontend.some(file => existsSync(file) && /python|backend\/|resolvePython|scripts\//i.test(readFileSync(file,'utf8')));
  return { tier, paths: sorted(paths), frontend: sorted(frontend), backend: sorted(backend), browser: tier === 'FULL' ? [] : browser,
    nativeChecks: tier === 'FULL' ? [] : sorted(nativeChecks),
    nativeCases: tier === 'FULL' ? {} : nativeCases,
    node, python, reasons, inventoryHash: createHash('sha256').update(JSON.stringify({inventory, certificate})).digest('hex') };
}

export function makeFastPlan(paths, context, options = {}) {
  const selection = selectFastLane(paths, options);
  const plan = { version: 1, ...selection, ...context, runtime };
  return { ...plan, hash: planHash(plan) };
}

export function validateFastGate(plan, report, needs, currentPullRequest, execution, fullPlan) {
  const failures = [];
  if (!plan || plan.version !== 1 || plan.hash !== planHash(plan) || needs.selection?.result !== 'success') return ['Missing or invalid selection plan'];
  if (!['FAST','STUDY','FULL'].includes(plan.tier)) failures.push('Unknown qualification tier');
  if (!currentPullRequest || currentPullRequest.number !== plan.pullRequest || currentPullRequest.state !== 'open'
    || currentPullRequest.head?.sha !== plan.head || currentPullRequest.base?.repo?.id !== plan.repositoryId
    || currentPullRequest.base.repo.full_name !== plan.repository) failures.push('PR identity or head changed');
  if (plan.draft !== false || currentPullRequest?.draft !== false) failures.push('Draft or changed PR readiness requires fresh ready qualification');
  if (currentPullRequest?.labels?.some(label => label.name === 'ci:full') && plan.tier !== 'FULL') failures.push('Complete qualification requested after selection');
  if (plan.tier === 'FULL') {
    if (currentPullRequest?.base?.sha !== plan.base) failures.push('Full integration base changed; fresh qualification required');
    if (needs.quality?.result !== 'success') failures.push('Complete qualification did not pass');
    // The same-run quality dependency validates layer evidence; bind its immutable
    // plan to this later PR read rather than duplicating the complete validator.
    if (!fullPlan || fullPlan.version !== 3 || fullPlan.hash !== planHash(fullPlan)
      || fullPlan.event !== 'pull_request' || fullPlan.tier !== 'qualification' || fullPlan.scope !== 'complete'
      || fullPlan.draft !== false || fullPlan.repository !== plan.repository || fullPlan.repositoryId !== plan.repositoryId
      || fullPlan.pullRequest !== plan.pullRequest || fullPlan.head !== plan.head || fullPlan.base !== plan.base
      || !fullPlan.commit || fullPlan.commit !== currentPullRequest?.merge_commit_sha) {
      failures.push('Missing, invalid or stale complete integration plan');
    }
    return failures;
  }
  if (needs.fast?.result !== 'success' || !report || report.status !== 'success' || !report.completed
    || report.planHash !== plan.hash || report.head !== plan.head || report.repository !== plan.repository
    || report.pullRequest !== plan.pullRequest || report.runId !== execution.id || report.attempt !== execution.attempt) return [...failures,'Missing, failed, cancelled or stale selected evidence'];
  if (report.runtime?.node !== `v${plan.runtime.node}` || report.runtime?.platform !== plan.runtime.platform || report.runtime?.architecture !== plan.runtime.architecture) failures.push('Execution runtime differs from selected runtime');
  if (plan.python && report.runtime?.python !== `Python ${plan.runtime.python}`) failures.push('Python runtime differs from selected runtime');
  if (!report.commands?.length || report.commands.some(command => command.exit_code !== 0 || command.error)) failures.push('Selected commands failed or did not execute');
  if (JSON.stringify(fastCommands(plan)) !== JSON.stringify((report.commands ?? []).map(({name,command,args}) => [name,command,args]))) failures.push('Missing selected command evidence');
  for (const layer of ['frontend','backend','browser']) {
    const selected = layer === 'browser' ? plan.browser : plan[layer];
    if (!selected.length) continue;
    const evidence = report[layer];
    if (!evidence?.expected?.length || !evidence.tests?.length || evidence.expected.length !== evidence.tests.length
      || new Set(evidence.expected.map(test => test.id)).size !== evidence.expected.length
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
    const stages = postgresTestStages({mode:'study',nativeChecks:plan.nativeChecks});
    if (!scenarios || scenarios.mode !== 'study' || scenarios.commit !== plan.head || scenarios.plan_hash !== plan.hash
      || JSON.stringify(scenarios.planned_stages) !== JSON.stringify(stages)
      || JSON.stringify(Object.keys(scenarios.stages ?? {}).sort()) !== JSON.stringify([...stages].sort())
      || stages.some(stage => scenarios.stages[stage]?.exit_code !== 0)) failures.push('Study durability evidence missing or failed');
    if ((plan.nativeChecks ?? []).length) {
      const native = report.native;
      if (!native || native.head !== plan.head || native.planHash !== plan.hash || native.runId !== execution.id
        || native.attempt !== execution.attempt || native.repository !== plan.repository
        || JSON.stringify(Object.keys(native.checks ?? {}).sort()) !== JSON.stringify(plan.nativeChecks)) {
        failures.push('Missing or stale native fixture evidence');
      } else for (const check of plan.nativeChecks) {
        const evidence = native.checks[check];
        if (!evidence?.expected?.length || !evidence.tests?.length
          || JSON.stringify(sorted(evidence.expected)) !== JSON.stringify(plan.nativeCases?.[check])
          || evidence.expected.length !== evidence.tests.length
          || new Set(evidence.expected).size !== evidence.expected.length
          || new Set(evidence.tests.map(test => test.id)).size !== evidence.tests.length
          || JSON.stringify(sorted(evidence.expected)) !== JSON.stringify(sorted(evidence.tests.map(test => test.id)))
          || evidence.tests.some(test => test.status !== 'passed' || test.retries !== 0)) failures.push('Native fixture cases missing, skipped, duplicated or failed');
      }
    }
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
    pullRequest:pr?.number ?? null,draft:pr?.draft ?? null,head:pr?.head.sha ?? process.env.GITHUB_SHA,base:pr?.base.sha ?? null}, {comparisonAvailable,complete});
  mkdirSync('test-results/fast-lane',{recursive:true});
  writeFileSync('test-results/fast-lane/plan.json',JSON.stringify(plan,null,2));
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT,`tier=${plan.tier}\nnode=${plan.node}\npython=${plan.python}\nhead=${plan.head}\n`);
  if (process.env.GITHUB_STEP_SUMMARY) appendFileSync(process.env.GITHUB_STEP_SUMMARY,`## Emergency qualification: ${plan.tier}\n\nHead: ${plan.head}\n\n${plan.reasons.map(reason => `- ${reason}`).join('\n')}\n\nSelected unit files: ${plan.frontend.length}; backend files: ${plan.backend.length}; browser cases: ${plan.browser.length}.\n`);
}
