import { readdirSync, realpathSync } from 'node:fs';
import { mkdtemp, mkdir, cp, rm, symlink, writeFile } from 'node:fs/promises';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { stageExternalInputs } from './build.mjs';

const group = process.argv[2] ?? 'pure';
const patterns = {
  pure: /(?:core|resources|artifact)\.test\.mjs$/,
  protocol: /(?:protocol|worker|wire)\.test\.mjs$/,
  engine: /^real-engine\.test\.mjs$/,
};
let temporary;
try {
  if (!patterns[group]) throw new Error('Unknown suite: ' + group);
  // Private inputs stay immutable for the entire child test run. Parallel runners
  // neither rewrite nor trust another invocation's ignored schemas/assets.
  temporary = await mkdtemp(join(tmpdir(), 'tempo-stockfish-tests-'));
  await mkdir(join(temporary, 'workers/stockfish'), { recursive: true });
  const component = realpathSync(join(temporary, 'workers/stockfish'));
  const localInputs = ['index.d.ts', 'package.json', 'package-lock.json', 'engine-descriptor.json', 'build-inputs.json', 'README.md', 'REGRESSIONS.md', 'licenses', 'tests', 'fixtures', 'python'];
  for (const name of readdirSync(import.meta.dirname).filter(name => name.endsWith('.mjs') || localInputs.includes(name))) {
    await cp(join(import.meta.dirname, name), join(component, name), { recursive: true });
  }
  await symlink(join(import.meta.dirname, 'node_modules'), join(component, 'node_modules'), 'dir');
  const manifest = await stageExternalInputs(component);
  await writeFile(join(component, 'build-inputs.json'), JSON.stringify(manifest, null, 2) + '\n');
  // Artifact tests build from the same verified source closure in a sparse repo.
  for (const input of manifest.external_inputs) {
    const source = resolve(temporary, input.source);
    if (!source.startsWith(temporary + sep)) throw new Error('Escaping test input source');
    await mkdir(dirname(source), { recursive: true });
    await cp(join(component, input.target), source);
  }
  const files = readdirSync(join(component, 'tests')).filter(name => patterns[group].test(name) && (group === 'engine' || name !== 'real-engine.test.mjs')).map(name => resolve(component, 'tests', name));
  if (!files.length) throw new Error('Zero selected test files');
  const args = ['--test', '--test-concurrency=1', '--test-reporter=' + join(component, 'tests/reporter.mjs'), ...(process.argv[3] ? ['--test-name-pattern=' + process.argv[3]] : []), ...files];
  const environment = { ...process.env }; delete environment.NODE_TEST_CONTEXT;
  const result = spawnSync(process.execPath, args, { cwd: component, env: environment, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  process.stderr.write(result.stderr ?? '');
  const report = { success: true, passed: 0, failed: 0, skipped: 0, executedIdentities: [] };
  let summary;
  for (const line of (result.stdout ?? '').split('\n').filter(Boolean)) {
    const event = JSON.parse(line);
    // Node can emit a passing file wrapper when the name filter selects no cases.
    const fileWrapper = event.data.line === 1 && event.data.column === 1 && typeof event.data.name === 'string' && resolve(component, event.data.name) === event.data.file;
    for (const key of ['file', 'entryFile']) if (event.data[key]?.startsWith(component + '/')) event.data[key] = import.meta.dirname + event.data[key].slice(component.length);
    if (event.type === 'test:stderr') process.stderr.write(event.data.message);
    if (event.type === 'test:stdout') process.stderr.write(event.data.message);
    if (event.type === 'test:fail') { report.failed++; process.stderr.write(JSON.stringify(event) + '\n'); }
    if (event.type === 'test:pass' && event.data.details?.type === 'test' && !fileWrapper) {
      if (event.data.skip || event.data.todo) report.skipped++;
      else { report.passed++; report.executedIdentities.push(`${event.data.file}:${event.data.line} ${event.data.name} [${report.passed}]`); }
    }
    if (event.type === 'test:summary' && !event.data.file) summary = event.data;
  }
  report.success = result.status === 0 && report.passed > 0 && report.failed === 0 && report.skipped === 0 && summary?.success === true && summary.counts.cancelled === 0 && summary.counts.skipped === 0 && summary.counts.todo === 0;
  console.log(JSON.stringify(report));
  if (!report.success) process.exitCode = 1;
} catch (error) { console.error(error.stack); process.exitCode = 1; }
finally { if (temporary) await rm(temporary, { recursive: true, force: true }); }
