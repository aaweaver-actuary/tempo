import { readFile, writeFile, mkdir, mkdtemp, cp, rm } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

export const componentRoot = import.meta.dirname;
export const runtimeFiles = ['index.mjs', 'index.d.ts', 'core.mjs', 'wire.mjs', 'engine.mjs', 'worker.mjs', 'child.mjs', 'resources.mjs', 'cli.mjs', 'engine-descriptor.json', 'README.md'];
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export async function stageExternalInputs(destination, { repositoryRoot = resolve(componentRoot, '../..') } = {}) {
  const manifest = JSON.parse(await readFile(join(componentRoot, 'build-inputs.json'), 'utf8'));
  for (const input of manifest.external_inputs) {
    const source = resolve(repositoryRoot, input.source), target = resolve(destination, input.target);
    if (!source.startsWith(repositoryRoot + sep) || !target.startsWith(resolve(destination) + sep)) throw new Error('Escaping build input');
    const bytes = await readFile(source);
    if (sha256(bytes) !== input.sha256) throw new Error('Changed immutable input: ' + input.source);
    await mkdir(dirname(target), { recursive: true }); await writeFile(target, bytes);
  }
  return manifest;
}
export async function buildArtifact({ outputDirectory = join(componentRoot, 'dist') } = {}) {
  await mkdir(outputDirectory, { recursive: true });
  const stage = await mkdtemp(join(tmpdir(), 'tempo-stockfish-build-'));
  try {
    const inputs = await stageExternalInputs(stage);
    for (const name of [...runtimeFiles, 'package.json', 'package-lock.json', 'licenses']) await cp(join(componentRoot, name), join(stage, name), { recursive: true });
    await cp(join(componentRoot, 'package-lock.json'), join(stage, 'npm-shrinkwrap.json'));
    const packed = spawnSync('npm', ['pack', '--json', '--ignore-scripts', '--workspaces=false', '--pack-destination', resolve(outputDirectory)], { cwd: stage, encoding: 'utf8', env: { ...process.env, npm_config_cache: join(stage, '.npm-cache') } });
    if (packed.status !== 0) throw new Error(packed.stderr || packed.stdout);
    const record = JSON.parse(packed.stdout)[0], artifactPath = join(outputDirectory, record.filename);
    return { name: record.filename, path: artifactPath, sha256: sha256(await readFile(artifactPath)), inputs, files: record.files.map(item => item.path) };
  } finally { await rm(stage, { recursive: true, force: true }); }
}
if (process.argv[1] && realpathSync(resolve(process.argv[1])) === import.meta.filename) {
  const out = process.argv.indexOf('--out');
  try { console.log(JSON.stringify(await buildArtifact({ outputDirectory: out < 0 ? undefined : resolve(process.argv[out + 1]) }))); }
  catch (error) { console.error(error.stack); process.exitCode = 1; }
}
