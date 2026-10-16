import { readFileSync, realpathSync, existsSync } from 'node:fs';
import { readFile, writeFile, mkdir, mkdtemp, cp, rm } from 'node:fs/promises';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { isBuiltin } from 'node:module';
import { spawn, spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';
import ts from 'typescript';
import { buildArtifact, componentRoot, runtimeFiles } from './build.mjs';
import { positions, jobFor } from './fixtures/jobs.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');
const option = (name, fallback) => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback;
function sourceState() {
  const revision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: componentRoot, encoding: 'utf8' });
  const status = spawnSync('git', ['status', '--porcelain'], { cwd: componentRoot, encoding: 'utf8' });
  return { source_revision: revision.status === 0 ? revision.stdout.trim() : null, source_dirty: status.status === 0 ? Boolean(status.stdout.trim()) : null };
}
function execute(executable, args, cwd, expected = 0) {
  const environment = { ...process.env }; delete environment.NODE_PATH; delete environment.NODE_OPTIONS;
  const result = spawnSync(executable, args, { cwd, encoding: 'utf8', env: environment, maxBuffer: 16 * 1024 * 1024 });
  if (result.error || result.status !== expected) throw new Error(`${executable} ${args.join(' ')} failed (${result.status}): ${result.stderr || result.stdout || result.error}`);
  return result.stdout;
}
function executeAsync(executable, args, cwd) {
  return new Promise((resolveOutput, reject) => {
    const child = spawn(executable, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.once('error', reject);
    child.once('exit', code => code === 0 ? resolveOutput(stdout) : reject(new Error(`${executable} failed (${code}): ${stderr}`)));
  });
}

export function checkBoundary(registration, { root = componentRoot } = {}) {
  const metadata = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
  if (metadata.name !== registration.artifact.name || metadata.version !== registration.artifact.version) throw new Error('Worker artifact identity differs from registration');
  if (JSON.stringify(Object.keys(metadata.dependencies).sort()) !== JSON.stringify([...registration.dependencies].sort())) throw new Error('Worker dependencies differ from registration');
  if (Object.keys(metadata.exports).join() !== '.' || metadata.exports['.'] !== './index.mjs') throw new Error('Worker public export map differs');
  const filenames = runtimeFiles.filter(name => name.endsWith('.mjs')).map(name => join(root, name));
  for (const filename of filenames) {
    if (!existsSync(filename) || !realpathSync(filename).startsWith(realpathSync(root) + sep)) throw new Error('Missing or escaping worker source');
    const syntax = ts.createSourceFile(filename, readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const imports = [];
    function visit(node) {
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) imports.push(node.moduleSpecifier.text);
      if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || ts.isIdentifier(node.expression) && node.expression.text === 'require')) {
        if (node.arguments.length !== 1 || !ts.isStringLiteralLike(node.arguments[0])) throw new Error('Unresolved worker dynamic loader');
        imports.push(node.arguments[0].text);
      }
      if ((ts.isIdentifier(node) && ['eval', 'createRequire'].includes(node.text)) || (ts.isPropertyAccessExpression(node) && ['require', '_load'].includes(node.name.text))) throw new Error('Unresolved worker dynamic loader');
      ts.forEachChild(node, visit);
    }
    visit(syntax);
    for (const imported of imports) {
      if (isBuiltin(imported)) continue;
      if (imported.startsWith('.')) {
        const target = resolve(dirname(filename), imported);
        if (!target.startsWith(root + sep) || !existsSync(target) || !realpathSync(target).startsWith(realpathSync(root) + sep)) throw new Error('Worker source escape or missing import');
        if (!filenames.includes(target)) throw new Error('Worker transitive source is absent from declared runtime files: ' + imported);
      } else {
        const dependency = imported.startsWith('@') ? imported.split('/').slice(0, 2).join('/') : imported.split('/')[0];
        if (!registration.dependencies.includes(dependency)) throw new Error('Undeclared worker dependency: ' + dependency);
      }
    }
    execute(process.execPath, ['--check', filename], root);
  }
  const program = ts.createProgram(filenames, { allowJs: true, moduleResolution: ts.ModuleResolutionKind.NodeNext, module: ts.ModuleKind.NodeNext, target: ts.ScriptTarget.ES2022, noEmit: true, skipLibCheck: true });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(join(root, 'index.mjs')))).map(symbol => symbol.name).sort();
  if (JSON.stringify(exports) !== JSON.stringify([...registration.publicExports].sort())) throw new Error('Worker exports differ from registration: ' + exports.join(', '));
  return { success: true, files: filenames.length, exports };
}

export async function qualifyRestricted({ reportPath, artifactsDirectory }) {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-installed-'));
  const report = { complete: false, ...sourceState(), node: process.version, artifacts: [], cases: { success: false, passed: 0, failed: 0, executedIdentities: [] } };
  try {
    const artifact = await buildArtifact({ outputDirectory: artifactsDirectory });
    report.artifacts.push({ name: artifact.name, sha256: artifact.sha256 });
    report.buildInputs = artifact.inputs;
    report.packagedFiles = artifact.files;
    execute('tar', ['-xzf', artifact.path, '--strip-components=1', '-C', temporary], temporary);
    await cp(join(componentRoot, 'package-lock.json'), join(temporary, 'package-lock.json'));
    execute('npm', ['ci', '--ignore-scripts', '--omit=dev', '--workspaces=false'], temporary);
    const installed = temporary;
    const identity = JSON.parse(execute(process.execPath, [join(installed, 'cli.mjs'), 'identity'], temporary));
    report.identity = identity;
    report.dependencies = JSON.parse(execute('npm', ['ls', '--omit=dev', '--all', '--json'], temporary));
    if (JSON.stringify(report.dependencies).match(/"(?:react|react-dom|next|pg|redis|celery|stockfish)"/)) throw new Error('Forbidden runtime dependency');
    execute(process.execPath, ['--input-type=module', '-e', 'import("@tempo/maia-worker").then(module => { if (!module.createModel || !module.inspectModel) throw Error("Missing public API"); })'], temporary);
    for (const input of artifact.inputs.external_inputs.filter(input => input.target)) if (hash(await readFile(join(installed, input.target))) !== input.sha256) throw new Error('Installed immutable input changed');
    if (hash(await readFile(join(installed, 'assets/model.onnx'))) !== artifact.inputs.model_sha256) throw new Error('Installed model changed');
    const identities = ['installed artifact public import exits without polling', 'installed artifact identity and model/vocabulary/schema checksums verified', 'installed dependency closure contains no Tempo services'];
    report.cases = { success: true, passed: identities.length, failed: 0, executedIdentities: identities };
    report.complete = true;
    return report;
  } catch (error) { report.error = error.stack; report.cases.failed = 1; throw error; }
  finally {
    await rm(temporary, { recursive: true, force: true });
    report.temporaryWorkspaceRemoved = !existsSync(temporary);
    await mkdir(dirname(reportPath), { recursive: true });
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  }
}

class ProtocolSession {
  constructor(image, containerName, transcript, { restart = false } = {}) {
    this.messages = []; this.events = []; this.waiters = []; this.transcript = transcript;
    const args = restart ? ['start', '--attach', '--interactive', containerName] : ['run', '--name', containerName, '--label', 'tempo.task=issue110-maia', '--network=none', '--cpus=1', '--memory=1g', '--memory-swap=1g', '--pids-limit=64', '--read-only', '-i', image, 'serve'];
    this.process = spawn('docker', args, { stdio: ['pipe', 'pipe', 'pipe'] });
    this.completion = new Promise(resolveExit => this.process.on('exit', (code, signal) => { this.exit = { code, signal }; resolveExit(this.exit); this.wake(); }));
    this.process.on('error', error => { this.error = error; this.wake(); });
    for (const [stream, target] of [[this.process.stdout, this.messages], [this.process.stderr, this.events]]) {
      let buffered = '';
      stream.on('data', chunk => {
        buffered += chunk;
        let newline;
        while ((newline = buffered.indexOf('\n')) >= 0) {
          const line = buffered.slice(0, newline); buffered = buffered.slice(newline + 1);
          if (!line.trim()) continue;
          try { const item = JSON.parse(line); target.push(item); if (target === this.messages) transcript.messages.push(item); }
          catch { transcript.diagnostics.push(line); }
        }
        this.wake();
      });
    }
  }
  wake() { for (const callback of this.waiters.splice(0)) callback(); }
  send(record) { this.process.stdin.write(JSON.stringify(record) + '\n'); }
  async wait(predicate, { events = false, start = 0, timeout = 180000 } = {}) {
    const deadline = performance.now() + timeout;
    while (true) {
      const item = (events ? this.events : this.messages).slice(start).find(predicate);
      if (item) return item;
      if (this.exit || this.error) throw new Error('Worker exited while awaiting record: ' + JSON.stringify({ exit: this.exit, error: String(this.error ?? ''), events: this.events, diagnostics: this.transcript.diagnostics }));
      if (performance.now() >= deadline) throw new Error('Timed out awaiting worker record: ' + JSON.stringify(this.events));
      await new Promise(resolveWait => { const timer = setTimeout(resolveWait, Math.min(250, deadline - performance.now())); this.waiters.push(() => { clearTimeout(timer); resolveWait(); }); });
    }
  }
}

export async function qualifyContainer({ reportPath, artifactsDirectory, exchangePath }) {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-container-'));
  const unique = randomUUID().slice(0, 8), image = `tempo-maia-issue110:${unique}`, containerName = `tempo-maia-issue110-${unique}`;
  const transcript = { exchanges: [], messages: [], diagnostics: [] };
  const report = { complete: false, ...sourceState(), started_at: new Date().toISOString(), resources: { image, containerName }, cases: { success: false, passed: 0, failed: 0, executedIdentities: [] } };
  let session;
  const passed = identity => report.cases.executedIdentities.push(identity);
  try {
    const restricted = await qualifyRestricted({ reportPath: join(temporary, 'restricted.json'), artifactsDirectory });
    report.artifacts = restricted.artifacts;
    await cp(join(artifactsDirectory, restricted.artifacts[0].name), join(temporary, 'artifact.tgz'));
    await cp(join(componentRoot, 'package-lock.json'), join(temporary, 'package-lock.json'));
    await cp(join(componentRoot, 'Dockerfile'), join(temporary, 'Dockerfile'));
    execute('docker', ['build', '--tag', image, '--file', join(temporary, 'Dockerfile'), temporary], temporary);
    report.image = JSON.parse(execute('docker', ['image', 'inspect', image], temporary))[0].Id;
    session = new ProtocolSession(image, containerName, transcript);
    const readyStarted = performance.now();
    const ready = await session.wait(event => event.event === 'ready', { events: true });
    report.readiness = ready;
    report.cold_initialization_ms = performance.now() - readyStarted;
    report.container = JSON.parse(execute('docker', ['inspect', containerName], temporary))[0];
    if (report.container.HostConfig.Memory !== 1073741824 || report.container.HostConfig.MemorySwap !== 1073741824 || report.container.HostConfig.NetworkMode !== 'none' || report.container.HostConfig.NanoCpus !== 1000000000 || !report.container.HostConfig.ReadonlyRootfs) throw new Error('Actual container profile differs');
    passed('offline restricted container is ready under verified 1 CPU / 1 GiB no-swap hard cap');
    const cancellationJob = jobFor(positions[0], { job_id: 'actual-cancellation' }), eventStart = session.events.length, messageStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: cancellationJob });
    const execution = await session.wait(event => event.event === 'execution_started' && event.job_id === cancellationJob.job_id, { events: true, start: eventStart });
    const cpuProofScript = `const fs=require('node:fs'); const p='/proc/${execution.pid}/stat'; const ticks=()=>{const s=fs.readFileSync(p,'utf8');const a=s.slice(s.lastIndexOf(')')+2).split(' ');return Number(a[11])+Number(a[12]);}; const initial=ticks();let attempts=0;const timer=setInterval(()=>{const current=ticks();if(current>initial){clearInterval(timer);console.log(JSON.stringify({pid:${execution.pid},initial_ticks:initial,observed_ticks:current}));}else if(++attempts>80){clearInterval(timer);process.exitCode=1;}},10);`;
    report.inflight_cpu = JSON.parse(await executeAsync('docker', ['exec', containerName, 'node', '-e', cpuProofScript], temporary));
    if (session.messages.slice(messageStart).some(item => item.model === 'MaiaResult' && item.payload.job_id === cancellationJob.job_id)) throw new Error('CPU-observed inference completed before cancellation could be submitted');
    const cancellation = { contract_version: 1, workspace_id: cancellationJob.workspace_id, job_id: cancellationJob.job_id, request_revision: 1, execution_generation: 1, cancellation_id: 'qualified-cancel' };
    session.send({ model: 'CancellationRequest', payload: cancellation });
    const acknowledgement = await session.wait(item => item.model === 'CancellationAcknowledgement' && item.payload.status === 'terminated', { start: messageStart });
    if (session.messages.slice(messageStart).some(item => item.model === 'MaiaResult' && item.payload.job_id === cancellationJob.job_id)) throw new Error('Terminated job produced a result');
    report.cancellation = acknowledgement;
    passed('actual ONNX execution cancellation waits for child termination');
    await session.wait(event => event.event === 'ready', { events: true, start: eventStart });
    const restored = jobFor(positions[1], { job_id: 'capacity-restored' }), capacityStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: restored });
    const restoredResult = await session.wait(item => item.model === 'MaiaResult' && item.payload.job_id === restored.job_id, { start: capacityStart });
    transcript.exchanges.push({ job: restored, result: restoredResult.payload });
    passed('terminated execution releases capacity for another actual job');
    for (const position of positions) {
      const job = jobFor(position), start = session.messages.length;
      session.send({ model: 'MaiaJob', payload: job });
      const record = await session.wait(item => item.model === 'MaiaResult' && item.payload.job_id === job.job_id, { start });
      const { validateResultForJob } = await import('./wire.mjs');
      validateResultForJob(job, record.payload);
      transcript.exchanges.push({ job, result: record.payload });
      passed('actual contract-valid inference ' + position.name);
    }
    const first = transcript.exchanges[0], replayStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: first.job });
    const replay = await session.wait(item => item.model === 'MaiaResult', { start: replayStart });
    if (JSON.stringify(replay.payload) !== JSON.stringify(first.result)) throw new Error('Replay changed completed result');
    passed('exact completed request replay preserves original result');
    const conflictStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: { ...first.job, rating: 1500 } });
    const conflict = await session.wait(item => item.model === 'JobStatus' && item.payload.status === 'failed', { start: conflictStart });
    if (!String(conflict.payload.reason).includes('conflict')) throw new Error('Conflicting identity did not fail explicitly');
    passed('immutable logical identity conflict rejected');
    for (const [name, changes, reason] of [['unsupported', { rating: 1150 }, 'unsupported_rating'], ['terminal', { fen: '7k/6Q1/5K2/8/8/8/8/8 b - - 0 1' }, 'terminal_position'], ['memory', { constraints: { max_wall_time_ms: 60000, max_memory_mb: 256 } }, 'memory_budget_unsupported']]) {
      const failedJob = jobFor(positions[0], { job_id: name, ...changes }), start = session.messages.length;
      session.send({ model: 'MaiaJob', payload: failedJob });
      const failure = await session.wait(item => item.model === 'JobStatus' && item.payload.status === 'failed' && item.payload.job_id === name, { start });
      if (failure.payload.reason !== reason) throw new Error('Wrong explicit failure: ' + JSON.stringify(failure));
      passed('explicit non-success ' + name);
    }
    session.process.stdin.end();
    const exit = await session.completion;
    if (exit.code !== 0) throw new Error('Worker shutdown failed: ' + JSON.stringify(exit));
    passed('worker drains and shuts down cleanly');
    report.exit = exit;
    session = new ProtocolSession(image, containerName, transcript, { restart: true });
    await session.wait(event => event.event === 'ready', { events: true });
    const restartedJob = { ...first.job, execution_generation: first.job.execution_generation + 1 };
    session.send({ model: 'MaiaJob', payload: restartedJob });
    const restartedResult = await session.wait(item => item.model === 'MaiaResult' && item.payload.job_id === first.job.job_id);
    if (JSON.stringify(restartedResult.payload.predictions) !== JSON.stringify(first.result.predictions)) throw new Error('Restarted same-runtime inference changed predictions');
    transcript.exchanges.push({ job: restartedJob, result: restartedResult.payload });
    transcript.stale_results = [{ job: restartedJob, result: first.result, current_generation: restartedJob.execution_generation }];
    passed('caller retry after complete worker restart safely reexecutes immutable request');
    const staleStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: first.job });
    const stale = await session.wait(item => item.model === 'JobStatus' && item.payload.job_id === first.job.job_id && item.payload.status === 'failed', { start: staleStart });
    if (stale.payload.reason !== 'stale_generation' || session.messages.slice(staleStart).some(item => item.model === 'MaiaResult')) throw new Error('Obsolete generation was not fenced');
    passed('installed worker rejects a stale generation without returning a result');
    const postRestartJob = jobFor(positions[1], { job_id: 'post-restart-fresh-job' }), postRestartStart = session.messages.length;
    session.send({ model: 'MaiaJob', payload: postRestartJob });
    const postRestart = await session.wait(item => item.model === 'MaiaResult' && item.payload.job_id === postRestartJob.job_id, { start: postRestartStart });
    transcript.exchanges.push({ job: postRestartJob, result: postRestart.payload });
    passed('restarted worker accepts another fresh actual inference');
    session.process.stdin.end();
    report.restarted_exit = await session.completion;
    if (report.restarted_exit.code !== 0) throw new Error('Restarted worker shutdown failed');
    report.complete = true;
    report.cases.success = true;
    report.cases.passed = report.cases.executedIdentities.length;
    return report;
  } catch (error) { report.error = error.stack; report.cases.failed = 1; throw error; }
  finally {
    if (session && !session.exit) { try { execute('docker', ['kill', containerName], temporary); } catch {} await session.completion; }
    try { report.finalContainer = JSON.parse(execute('docker', ['inspect', containerName], temporary))[0]; } catch {}
    try { execute('docker', ['rm', containerName], temporary); report.containerRemoved = true; } catch (error) { report.containerCleanup = String(error); }
    try { execute('docker', ['image', 'rm', image], temporary); report.imageTagRemoved = true; } catch (error) { report.imageCleanup = String(error); }
    await rm(temporary, { recursive: true, force: true });
    report.temporaryWorkspaceRemoved = !existsSync(temporary);
    report.finished_at = new Date().toISOString();
    await mkdir(dirname(exchangePath), { recursive: true });
    await writeFile(exchangePath, JSON.stringify(transcript, null, 2) + '\n');
    await mkdir(dirname(reportPath), { recursive: true });
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  }
}

export async function qualifyBenchmark({ reportPath, artifactsDirectory }) {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-benchmark-'));
  const unique = randomUUID().slice(0, 8), image = `tempo-maia-benchmark:${unique}`, containerName = `tempo-maia-benchmark-${unique}`;
  let report = { complete: false, ...sourceState(), resources: { image, containerName } };
  try {
    const artifact = await buildArtifact({ outputDirectory: artifactsDirectory });
    await cp(artifact.path, join(temporary, 'artifact.tgz'));
    await cp(join(componentRoot, 'package-lock.json'), join(temporary, 'package-lock.json'));
    await cp(join(componentRoot, 'Dockerfile'), join(temporary, 'Dockerfile'));
    execute('docker', ['build', '--tag', image, '--file', join(temporary, 'Dockerfile'), temporary], temporary);
    const output = execute('docker', ['run', '--name', containerName, '--label', 'tempo.task=issue110-maia-benchmark', '--network=none', '--cpus=1', '--memory=1g', '--memory-swap=1g', '--pids-limit=64', '--read-only', '--mount', `type=bind,source=${join(componentRoot, 'benchmark.mjs')},target=/tmp/benchmark.mjs,readonly`, '--mount', `type=bind,source=${join(componentRoot, 'fixtures/positions.json')},target=/tmp/positions.json,readonly`, '--entrypoint=node', image, '/tmp/benchmark.mjs', '--package-root', '/opt/maia', '--positions', '/tmp/positions.json'], temporary);
    report = { ...report, ...JSON.parse(output), artifacts: [{ name: artifact.name, sha256: artifact.sha256 }], complete: true };
    report.container = JSON.parse(execute('docker', ['inspect', containerName], temporary))[0];
    if (report.exit_status !== 0 || report.inference_count !== 26 || report.container.State.OOMKilled) throw new Error('Benchmark failed or inference count differs');
    return report;
  } catch (error) { report.complete = false; report.error = error.stack; throw error; }
  finally {
    try { execute('docker', ['rm', '-f', containerName], temporary); report.containerRemoved = true; } catch (error) { report.containerCleanup = String(error); }
    try { execute('docker', ['image', 'rm', image], temporary); report.imageTagRemoved = true; } catch (error) { report.imageCleanup = String(error); }
    await rm(temporary, { recursive: true, force: true });
    report.temporaryWorkspaceRemoved = !existsSync(temporary);
    await mkdir(dirname(reportPath), { recursive: true });
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  }
}

if (process.argv[1] && realpathSync(resolve(process.argv[1])) === import.meta.filename) {
  try {
    const command = process.argv[2];
    if (command === 'boundary') console.log(JSON.stringify(checkBoundary(JSON.parse(readFileSync(0, 'utf8')))));
    else if (['restricted', 'container', 'benchmark'].includes(command)) {
      const reportPath = resolve(option('--report', join(componentRoot, 'dist', command + '.json')));
      const artifactsDirectory = resolve(option('--artifacts', join(componentRoot, 'dist/artifacts')));
      await mkdir(artifactsDirectory, { recursive: true });
      const args = { reportPath, artifactsDirectory, exchangePath: resolve(option('--exchange', join(dirname(reportPath), 'exchange.json'))) };
      const report = await (command === 'container' ? qualifyContainer(args) : command === 'benchmark' ? qualifyBenchmark(args) : qualifyRestricted(args));
      console.log(JSON.stringify(command === 'container' ? report.cases : report));
    } else throw new Error('Expected boundary, restricted, container, or benchmark qualifier');
  } catch (error) { console.error(error.stack); process.exitCode = 1; }
}
