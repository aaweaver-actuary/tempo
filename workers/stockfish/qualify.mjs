import { readFileSync, realpathSync, existsSync, readdirSync } from 'node:fs';
import { readFile, writeFile, mkdir, mkdtemp, cp, rm } from 'node:fs/promises';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { isBuiltin } from 'node:module';
import { spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import ts from 'typescript';
import { buildArtifact, componentRoot, runtimeFiles } from './build.mjs';

const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const option = (name, fallback) => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback;
const python = () => process.env.TEMPO_STOCKFISH_PYTHON ?? process.env.TEMPO_COMPONENT_PYTHON ?? 'python3';
function execute(executable, args, cwd = componentRoot, expected = 0, extraEnv = {}) {
  const env = { ...process.env, ...extraEnv }; delete env.NODE_PATH; delete env.NODE_OPTIONS; delete env.PYTHONPATH; delete env.PYTHONHOME;
  const result = spawnSync(executable, args, { cwd, encoding: 'utf8', env, maxBuffer: 16 * 1024 * 1024 });
  if (result.error || result.status !== expected) throw new Error(`${executable} ${args.join(' ')} failed (${result.status}): ${(result.stderr || result.stdout || String(result.error)).slice(-6000)}`);
  return result.stdout;
}
function sourceState() {
  return { source_revision: execute('git', ['rev-parse', 'HEAD']).trim(), source_dirty: Boolean(execute('git', ['status', '--porcelain']).trim()) };
}
export function checkBoundary(registration, { root = componentRoot } = {}) {
  const metadata = JSON.parse(readFileSync(join(root, 'package.json')));
  if (metadata.name !== registration.artifact.name || metadata.version !== registration.artifact.version || JSON.stringify(Object.keys(metadata.dependencies).sort()) !== JSON.stringify([...registration.dependencies].sort())) throw new Error('Worker registration/metadata differs');
  const external = JSON.parse(readFileSync(join(root, 'build-inputs.json'))).external_inputs;
  const sources = runtimeFiles.filter(name => name.endsWith('.mjs')).map(name => resolve(root, name));
  for (const filename of sources) {
    if (!realpathSync(filename).startsWith(realpathSync(root) + sep)) throw new Error('Escaping runtime source');
    const syntax = ts.createSourceFile(filename, readFileSync(filename, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const imports = [];
    function visit(node) {
      if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) imports.push(node.moduleSpecifier.text);
      if (ts.isCallExpression(node) && (node.expression.kind === ts.SyntaxKind.ImportKeyword || ts.isIdentifier(node.expression) && node.expression.text === 'require')) {
        if (node.arguments.length !== 1 || !ts.isStringLiteralLike(node.arguments[0])) throw new Error('Unresolved runtime loader');
        imports.push(node.arguments[0].text);
      }
      if (ts.isIdentifier(node) && ['eval', 'createRequire'].includes(node.text)) throw new Error('Unresolved runtime loader');
      ts.forEachChild(node, visit);
    }
    visit(syntax);
    for (const imported of imports) {
      if (isBuiltin(imported)) continue;
      if (imported.startsWith('.')) {
        const target = resolve(dirname(filename), imported);
        if (!target.startsWith(resolve(root) + sep)) throw new Error('Runtime import escape');
        if (sources.includes(target)) continue;
        if (!external.some(input => resolve(root, input.target) === target)) throw new Error('Undeclared runtime source: ' + imported);
      } else if (!Object.keys(metadata.dependencies).some(dependency => imported === dependency || imported.startsWith(dependency + '/'))) throw new Error('Undeclared dependency');
    }
    execute(process.execPath, ['--check', filename], root);
  }
  const program = ts.createProgram(sources, { allowJs: true, module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext, noEmit: true, skipLibCheck: true });
  const checker = program.getTypeChecker();
  const exports = checker.getExportsOfModule(checker.getSymbolAtLocation(program.getSourceFile(join(root, 'index.mjs')))).map(symbol => symbol.name).sort();
  if (JSON.stringify(exports) !== JSON.stringify([...registration.publicExports].sort())) throw new Error('Public exports differ');
  return { success: true, files: sources.length, exports };
}

export async function qualifyRestricted({ reportPath, artifactsDirectory }) {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-stockfish-restricted-'));
  const report = { complete: false, ...sourceState(), node: process.version, artifacts: [], cases: { success: false, passed: 0, failed: 0, executedIdentities: [] } };
  const passed = name => report.cases.executedIdentities.push(name);
  try {
    await mkdir(artifactsDirectory, { recursive: true });
    const artifact = await buildArtifact({ outputDirectory: artifactsDirectory });
    report.artifacts.push({ name: artifact.name, sha256: artifact.sha256 }); report.packagedFiles = artifact.files; report.buildInputs = artifact.inputs;
    const runtime = join(temporary, 'runtime'); await mkdir(runtime);
    execute('tar', ['-xzf', artifact.path, '--strip-components=1', '-C', runtime], temporary);
    execute('npm', ['ci', '--ignore-scripts', '--omit=dev', '--workspaces=false'], runtime, 0, { npm_config_cache: join(temporary, 'npm-cache') });
    report.identity = JSON.parse(execute(process.execPath, [join(runtime, 'cli.mjs'), 'identity'], temporary));
    const dependencies = JSON.parse(execute('npm', ['ls', '--omit=dev', '--all', '--json'], runtime));
    if (JSON.stringify(dependencies).match(/"(?:react|react-dom|next|pg|redis|celery|onnxruntime-web)"/)) throw new Error('Forbidden dependency');
    report.dependencies = dependencies;
    execute(process.execPath, ['-e', `import(${JSON.stringify(join(runtime, 'index.mjs'))}).then(m=>{if(!m.StockfishWorker||!m.stockfishInputDigest||!m.inspectEngine)throw Error('exports');})`], temporary);
    passed('installed runtime public import is passive');
    for (const input of artifact.inputs.external_inputs) if (hash(await readFile(join(runtime, input.target))) !== input.sha256) throw new Error('Installed immutable input mismatch');
    if (artifact.files.some(name => /^(?:app|backend|services|node_modules|tests)\//.test(name))) throw new Error('Unrelated repository source packaged');
    passed('B_restricted_artifact');
    // Deliberately corrupt a staged engine asset: identity must refuse readiness.
    await writeFile(join(runtime, 'assets/sf_19_smallnet.wasm'), 'corrupted');
    execute(process.execPath, [join(runtime, 'cli.mjs'), 'identity'], temporary, 1);
    passed('installed engine asset mismatch is rejected');
    for (const [source, name] of [[join(componentRoot, 'python'), 'client'], [resolve(componentRoot, '../../contracts/analysis-jobs'), 'contract']]) {
      const stage = join(temporary, name); await mkdir(stage);
      for (const input of ['pyproject.toml', 'src', ...(name === 'contract' ? ['schemas'] : [])]) await cp(join(source, input), join(stage, input), { recursive: true });
      execute(python(), ['-m', 'build', '--no-isolation', '--outdir', artifactsDirectory, stage], temporary);
    }
    for (const name of readdirSync(artifactsDirectory).filter(name => /^(?:tempo_stockfish_client|tempo_analysis_jobs_contracts)-.*\.(?:whl|tar\.gz)$/.test(name))) report.artifacts.push({ name, sha256: hash(await readFile(join(artifactsDirectory, name))) });
    const clientWheel = join(artifactsDirectory, 'tempo_stockfish_client-1.0.0-py3-none-any.whl');
    const contractWheel = join(artifactsDirectory, 'tempo_analysis_jobs_contracts-1.0.0-py3-none-any.whl');
    for (const version of ['3.12', '3.14']) {
      const environment = join(temporary, 'python-' + version), interpreter = join(environment, 'bin/python');
      const uvEnv = { UV_CACHE_DIR: process.env.UV_CACHE_DIR ?? join(temporary, 'uv-cache') };
      execute('uv', ['venv', '--python', version, environment], temporary, 0, uvEnv);
      execute('uv', ['pip', 'install', '--python', interpreter, '--require-hashes', '-r', resolve(componentRoot, '../../contracts/requirements-test.lock')], temporary, 0, uvEnv);
      execute('uv', ['pip', 'install', '--python', interpreter, 'chess==1.11.2'], temporary, 0, uvEnv);
      execute('uv', ['pip', 'install', '--python', interpreter, '--no-deps', contractWheel, clientWheel], temporary, 0, uvEnv);
      const tests = join(temporary, 'client-tests-' + version); await mkdir(tests);
      await cp(join(componentRoot, 'tests/contracts'), join(tests, 'tests/contracts'), { recursive: true });
      await cp(join(componentRoot, 'fixtures'), join(tests, 'fixtures'), { recursive: true });
      const junit = join(temporary, 'client-' + version + '.xml');
      const output = execute(interpreter, ['-I', '-m', 'pytest', join(tests, 'tests/contracts/test_exchange.py'), '--import-mode=importlib', '-q', '--junitxml=' + junit], temporary, 0,
        { TEMPO_STOCKFISH_TEST_RUNTIME: runtime, TEMPO_STOCKFISH_TEST_NODE: process.execPath });
      const cases = JSON.parse(execute(interpreter, ['-I', '-c', 'import json,sys,xml.etree.ElementTree as E; root=E.parse(sys.argv[1]).getroot(); print(json.dumps([{**case.attrib,"failed":case.find("failure") is not None or case.find("error") is not None,"skipped":case.find("skipped") is not None} for case in root.iter("testcase")]))', junit], temporary));
      if (!cases.length || cases.some(item => item.failed || item.skipped)) throw new Error('Installed Python suite lacks complete passing case evidence');
      (report.pythonSuites ??= []).push({ version, output: output.trim(), cases });
      for (const item of cases) passed('installed Python ' + version + ' ' + item.classname + '::' + item.name);
    }
    report.complete = true; report.cases.success = true;
    report.cases.passed = report.cases.executedIdentities.length;
    return report;
  } catch (error) { report.error = error.stack; report.cases.failed = 1; throw error; }
  finally {
    await rm(temporary, { recursive: true, force: true }); report.temporaryWorkspaceRemoved = !existsSync(temporary);
    await mkdir(dirname(reportPath), { recursive: true }); await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
  }
}

export async function qualifyContainer({ reportPath, artifactsDirectory, exchangePath, benchmark = false }) {
  const builtSource = sourceState();
  await mkdir(artifactsDirectory, { recursive: true });
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-stockfish-image-'));
  try {
    const artifact = await buildArtifact({ outputDirectory: artifactsDirectory });
    await cp(artifact.path, join(temporary, 'artifact.tgz')); await cp(join(componentRoot, 'Dockerfile'), join(temporary, 'Dockerfile'));
    const image = 'tempo-stockfish-b:' + randomUUID().slice(0, 8);
    execute('docker', ['build', '--label', 'tempo.task=analysis-b-stockfish', '--tag', image, temporary], temporary);
    execute(python(), ['-I', join(componentRoot, 'qualify_container.py'), '--image', image, '--report', reportPath, '--exchange', exchangePath, ...(benchmark ? ['--benchmark'] : [])], temporary);
    const report = JSON.parse(await readFile(reportPath, 'utf8'));
    report.artifacts = [{ name: artifact.name, sha256: artifact.sha256 }]; Object.assign(report, builtSource);
    report.cases = { success: report.complete, passed: report.cases.length, failed: 0, executedIdentities: report.cases.map(item => item.name), details: report.cases };
    await writeFile(reportPath, JSON.stringify(report, null, 2) + '\n');
    return report;
  } finally { await rm(temporary, { recursive: true, force: true }); }
}

async function main() {
  const mode = process.argv[2];
  const reportPath = resolve(option('--report', join(componentRoot, 'dist', mode + '.json')));
  const artifactsDirectory = resolve(option('--artifacts', join(dirname(reportPath), 'artifacts')));
  if (mode === 'boundary') { let input=''; for await (const chunk of process.stdin) input+=chunk; console.log(JSON.stringify(checkBoundary(JSON.parse(input)))); return; }
  const report = mode === 'restricted' ? await qualifyRestricted({ reportPath, artifactsDirectory }) : ['container', 'benchmark'].includes(mode) ? await qualifyContainer({ reportPath, artifactsDirectory, exchangePath: resolve(option('--exchange', join(dirname(reportPath), mode + '-exchange.json'))), benchmark: mode === 'benchmark' }) : null;
  if (!report) throw new Error('Unknown qualifier');
  console.log(JSON.stringify({ ...report.cases, report: reportPath, complete: report.complete }));
}
if (process.argv[1] && realpathSync(resolve(process.argv[1])) === import.meta.filename) main().catch(error => { console.error(error.stack); process.exitCode=1; });
