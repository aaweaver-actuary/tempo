import { readFile, writeFile, mkdir, mkdtemp, cp, rm } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { resolve, join, dirname, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

export const componentRoot = import.meta.dirname;
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export const runtimeFiles = ['index.mjs','core.mjs','model.mjs','errors.mjs','worker.mjs','child.mjs','cli.mjs','wire.mjs','resources.mjs','model-descriptor.json','README.md'];

export async function stageExternalInputs(destination, { repositoryRoot = resolve(componentRoot, '../..') } = {}) {
  await mkdir(destination, { recursive: true });
  const manifest = JSON.parse(await readFile(join(componentRoot, 'build-inputs.json'), 'utf8'));
  const parts = [];
  for (const input of manifest.external_inputs) {
    const source = resolve(repositoryRoot, input.source);
    if (!source.startsWith(repositoryRoot + sep)) throw new Error('Build input escapes repository');
    const bytes = await readFile(source);
    if (sha256(bytes) !== input.sha256) throw new Error('Changed immutable build input: ' + input.source);
    if (input.role === 'model-part') parts.push(bytes);
    else {
      const target = resolve(destination, input.target);
      if (!target.startsWith(resolve(destination) + sep)) throw new Error('Build target escapes artifact');
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, bytes);
    }
  }
  const model = Buffer.concat(parts);
  if (sha256(model) !== manifest.model_sha256) throw new Error('Reconstructed model checksum mismatch');
  await mkdir(join(destination, 'assets'), { recursive: true });
  await writeFile(join(destination, 'assets/model.onnx'), model);
  return manifest;
}

export async function stageInputs(destination, options) {
  const manifest = await stageExternalInputs(destination, options);
  for (const name of [...runtimeFiles, 'package.json', 'package-lock.json']) await cp(join(componentRoot, name), join(destination, name));
  // npm excludes package-lock from published packages; shrinkwrap preserves the
  // same exact transitive graph for tarball and container installations.
  await cp(join(componentRoot, 'package-lock.json'), join(destination, 'npm-shrinkwrap.json'));
  return manifest;
}

export async function buildArtifact({ outputDirectory = join(componentRoot, 'dist') } = {}) {
  await mkdir(outputDirectory, { recursive: true });
  const stage = await mkdtemp(join(tmpdir(), 'tempo-maia-build-'));
  try {
    const inputs = await stageInputs(stage);
    const packed = spawnSync('npm', ['pack', '--json', '--ignore-scripts', '--workspaces=false', '--pack-destination', resolve(outputDirectory)], { cwd: stage, encoding: 'utf8', env: { ...process.env, npm_config_cache: join(stage, '.npm-cache') } });
    if (packed.status !== 0) throw new Error(packed.stderr || packed.stdout);
    const record = JSON.parse(packed.stdout)[0];
    const artifactPath = join(outputDirectory, record.filename);
    return { name: record.filename, path: artifactPath, sha256: sha256(await readFile(artifactPath)), inputs, files: record.files.map(item => item.path) };
  } finally { await rm(stage, { recursive: true, force: true }); }
}

if (process.argv[1] && realpathSync(resolve(process.argv[1])) === import.meta.filename) {
  const outputIndex = process.argv.indexOf('--out');
  try { console.log(JSON.stringify(await buildArtifact({ outputDirectory: outputIndex < 0 ? undefined : resolve(process.argv[outputIndex + 1]) }))); }
  catch (error) { console.error(error.stack); process.exitCode = 1; }
}
