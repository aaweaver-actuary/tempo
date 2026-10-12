import { readFileSync, existsSync, appendFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { validateFastGate } from './ci-fast-lane.mjs';
const read = path => existsSync(path) ? JSON.parse(readFileSync(path,'utf8')) : null;
const plan = read('test-results/fast-lane/plan.json');
const needs = JSON.parse(process.env.TEMPO_CI_NEEDS ?? '{}');
let failures;
if (process.env.GITHUB_EVENT_NAME === 'pull_request') {
  const response = spawnSync('gh',['api',`repos/${process.env.GITHUB_REPOSITORY}/pulls/${process.env.TEMPO_PR}`],{encoding:'utf8'});
  failures = response.status === 0 ? validateFastGate(plan,read('test-results/fast-lane/result.json'),needs,JSON.parse(response.stdout),
    {id:Number(process.env.GITHUB_RUN_ID),attempt:Number(process.env.GITHUB_RUN_ATTEMPT)},read('test-results/ci/plan.json')) : ['Current PR identity unavailable'];
} else failures = plan?.tier === 'FULL' && needs.selection?.result === 'success' && needs.quality?.result === 'success' ? [] : ['Complete boundary qualification did not pass'];
if (plan?.repository !== process.env.GITHUB_REPOSITORY) failures.push('Repository mismatch');
if (plan?.tier !== 'FULL' && spawnSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).stdout?.trim() !== plan?.head) failures.push('Aggregation checkout differs from selected PR head');
const verdict = {tier:plan?.tier,head:plan?.head,success:failures.length === 0,failures};
mkdirSync('test-results/fast-lane',{recursive:true});
writeFileSync('test-results/fast-lane/verdict.json',JSON.stringify(verdict,null,2));
console.log(JSON.stringify(verdict,null,2));
if (process.env.GITHUB_STEP_SUMMARY) {
  const report = read('test-results/fast-lane/result.json');
  appendFileSync(process.env.GITHUB_STEP_SUMMARY,`## merge-gate: ${verdict.success ? 'passed' : 'failed'} (${plan?.tier ?? 'unknown'})\n\nHead: ${plan?.head}\n\n${failures.map(failure => `- ${failure}`).join('\n')}\n\n`);
  if (report) appendFileSync(process.env.GITHUB_STEP_SUMMARY,`| Command | Seconds | Exit |\n| --- | ---: | ---: |\n${report.commands.map(command => `| ${command.name} | ${command.duration_seconds.toFixed(2)} | ${command.exit_code} |`).join('\n')}\n`);
}
process.exitCode = verdict.success ? 0 : 1;
