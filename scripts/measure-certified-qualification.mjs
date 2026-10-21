// Read-only classification replay; collection is not execution or a speed benchmark.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { selectFastLane, fastInventory } from './ci-fast-lane.mjs';
import { createPlan } from './ci-verification-plan.mjs';
import { postgresTestStages } from '../database/tests/postgres-test-plan.mjs';

const baseline = process.argv[2] ?? 'c5f773b18c4a5b30a3abda84ecac01ab625a3902';
function git(...args) {
  const result = spawnSync('git', args, {encoding:'utf8'});
  if (result.status !== 0) throw new Error(`Git read failed: ${args[0]}`);
  return result.stdout;
}
const originalInventory = JSON.parse(git('show',`${baseline}:scripts/ci-fast-lane-inventory.json`));
let originalSource = git('show',`${baseline}:scripts/ci-fast-lane.mjs`).split('function git(args)')[0];
originalSource = originalSource.replace(/export const fastInventory = .*?;/,
  `export const fastInventory = ${JSON.stringify(originalInventory)};`)
  .replace(/from '\.\/([^']+)'/g, (_, path) => `from '${new URL(path,import.meta.url).href}'`);
const before = await import(`data:text/javascript;base64,${Buffer.from(originalSource).toString('base64')}`);
const complete = createPlan({complete:true});
const files = git('ls-files').trim().split('\n');
const totalFrontendFiles = files.filter(path => /^tests\/unit\/.*\.test\.tsx?$/.test(path)).length;
const totalBackendFiles = files.filter(path => /^backend\/tests\/.*test_[^/]+\.py$/.test(path)).length;
const fullBrowserCases = complete.collection.filter(test => test.selected).length;
function describe(selection) {
  const full = selection.tier === 'FULL';
  return {tier:selection.tier,frontendFiles:full ? totalFrontendFiles : selection.frontend.length,
    backendFiles:full ? totalBackendFiles : selection.backend.length,
    browserCases:full ? fullBrowserCases : selection.browser.length,
    postgresStages:full ? postgresTestStages({mode:'durability'}) : selection.tier === 'STUDY'
      ? postgresTestStages({mode:'study',nativeChecks:selection.nativeChecks}) : [],
    nativeCases:selection.nativeCases ?? {}, reasons:selection.reasons};
}
const candidateSets = Object.fromEntries(fastInventory.certifiedTests.map(mapping => [mapping.paths[0],mapping.paths]));
candidateSets.prose = ['docs/testing.md','tests/REGRESSIONS.md'];
candidateSets.preparedRegression = ['tests/unit/prepared-review-continuation-regressions.test.ts','backend/tests/test_prepared_review_continuation.py'];
candidateSets.sharedRegistration = ['database/tests/test-postgres-docker.mjs','scripts/ci-verification-inventory.json'];
const report = {baseline,candidate:git('rev-parse','HEAD').trim(),
  note:'Same current checkout and path sets; historical-source replay only. FULL counts exclude pinned cases and auxiliary unit directories.',
  pinnedCases:complete.pinnedCollection.length,
  rows:Object.entries(candidateSets).map(([name,paths]) => ({name,paths,before:describe(before.selectFastLane(paths)),after:describe(selectFastLane(paths))})),
  certificate:JSON.parse(readFileSync('scripts/ci-certified-tests.json','utf8'))};
console.log(JSON.stringify(report,null,2));
