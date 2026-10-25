import { readdirSync, existsSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { spawnSync } from 'node:child_process';

const group = process.argv[2] ?? 'pure';
const patterns = {
  pure: /(?:core|model|artifact)\.test\.mjs$/,
  model: /^real-model\.test\.mjs$/,
};
try {
  if (!existsSync(join(import.meta.dirname, 'schemas/v1/MaiaJob.schema.json'))) {
    const { stageExternalInputs } = await import('./build.mjs');
    await stageExternalInputs(import.meta.dirname);
  }
  if (!patterns[group]) throw new Error('Unknown suite: ' + group);
  const files = readdirSync(join(import.meta.dirname, 'tests')).filter(name => patterns[group].test(name) && (group === 'model' || name !== 'real-model.test.mjs')).map(name => resolve(import.meta.dirname, 'tests', name));
  if (!files.length) throw new Error('Zero selected test files');
  const args = ['--test', '--test-concurrency=1', '--test-reporter=' + join(import.meta.dirname, 'tests/reporter.mjs'), ...(process.argv[3] ? ['--test-name-pattern=' + process.argv[3]] : []), ...files];
  const result = spawnSync(process.execPath, args, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  process.stderr.write(result.stderr ?? '');
  const report = { success: true, passed: 0, failed: 0, skipped: 0, executedIdentities: [] };
  let summary;
  for (const line of (result.stdout ?? '').split('\n').filter(Boolean)) {
    const event = JSON.parse(line);
    if (event.type === 'test:stderr') process.stderr.write(event.data.message);
    if (event.type === 'test:stdout') process.stderr.write(event.data.message);
    if (event.type === 'test:fail') { report.failed++; process.stderr.write(JSON.stringify(event) + '\n'); }
    if (event.type === 'test:pass' && event.data.details?.type === 'test' && !(event.data.name === event.data.file && event.data.line === 1 && event.data.column === 1)) {
      if (event.data.skip || event.data.todo) report.skipped++;
      else { report.passed++; report.executedIdentities.push(`${event.data.file}:${event.data.line} ${event.data.name} [${report.passed}]`); }
    }
    if (event.type === 'test:summary' && !event.data.file) summary = event.data;
  }
  report.success = result.status === 0 && report.passed > 0 && report.failed === 0 && summary?.success === true && summary.counts.cancelled === 0;
  console.log(JSON.stringify(report));
  if (!report.success) process.exitCode = 1;
} catch (error) { console.error(error.stack); process.exitCode = 1; }
