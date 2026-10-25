import { readFileSync, realpathSync, existsSync } from 'node:fs';
import { readFile, writeFile, mkdir, mkdtemp, cp, rm } from 'node:fs/promises';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { isBuiltin } from 'node:module';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import ts from 'typescript';
import { buildArtifact, componentRoot, runtimeFiles } from './build.mjs';

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
    const identity = JSON.parse(execute(process.execPath, ['--input-type=module', '-e', 'import("@tempo/maia-worker").then(async module => console.log(JSON.stringify(await module.inspectModel())))'], temporary));
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

if (process.argv[1] && realpathSync(resolve(process.argv[1])) === import.meta.filename) {
  try {
    const command = process.argv[2];
    if (command === 'boundary') console.log(JSON.stringify(checkBoundary(JSON.parse(readFileSync(0, 'utf8')))));
    else if (command === 'restricted') {
      const reportPath = resolve(option('--report', join(componentRoot, 'dist/restricted.json')));
      const artifactsDirectory = resolve(option('--artifacts', join(componentRoot, 'dist/artifacts')));
      await mkdir(artifactsDirectory, { recursive: true });
      console.log(JSON.stringify(await qualifyRestricted({ reportPath, artifactsDirectory })));
    } else throw new Error('Expected boundary or restricted qualifier');
  } catch (error) { console.error(error.stack); process.exitCode = 1; }
}
