import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, readFile, writeFile, mkdir, cp, rm, appendFile, symlink } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { stageExternalInputs, runtimeFiles, componentRoot } from '../build.mjs';
import { checkBoundary } from '../qualify.mjs';
import * as publicInterface from '../index.mjs';

test('immutable declared artifact inputs reconstruct the exact model, vocabularies and unmodified v1 schemas', async () => {
  const stage = await mkdtemp(join(tmpdir(), 'tempo-maia-artifact-test-'));
  try {
    const manifest = await stageExternalInputs(stage);
    const bytes = await readFile(join(stage, 'assets/model.onnx'));
    assert.equal(bytes.length, 45683686);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), manifest.model_sha256);
    for (const entry of manifest.external_inputs.filter(input => input.target)) assert.equal(createHash('sha256').update(await readFile(join(stage, entry.target))).digest('hex'), entry.sha256);
  } finally { await rm(stage, { recursive: true, force: true }); }
});

test('artifact builder rejects a changed declared source input before packaging', async () => {
  const stage = await mkdtemp(join(tmpdir(), 'tempo-maia-artifact-corruption-'));
  try {
    const sourceRoot = join(stage, 'source');
    await mkdir(join(sourceRoot, 'public/maia3/parts'), { recursive: true });
    await writeFile(join(sourceRoot, 'public/maia3/parts/part-00'), 'corrupt model');
    await assert.rejects(stageExternalInputs(join(stage, 'target'), { repositoryRoot: sourceRoot }), /Changed immutable build input/);
  } finally { await rm(stage, { recursive: true, force: true }); }
});

test('packaged boundary rejects transitive lazy escapes and unlisted local helpers', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-boundary-test-'));
  try {
    for (const name of runtimeFiles.filter(name => name.endsWith('.mjs')).concat('package.json')) await cp(join(componentRoot, name), join(temporary, name));
    const metadata = JSON.parse(await readFile(join(componentRoot, 'package.json'), 'utf8'));
    const registration = { artifact: { name: metadata.name, version: metadata.version }, dependencies: Object.keys(metadata.dependencies), publicExports: Object.keys(publicInterface) };
    assert.equal(checkBoundary(registration, { root: temporary }).success, true);
    await appendFile(join(temporary, 'core.mjs'), '\nexport async function forbiddenTransitiveLoader() { return import("../../backend/private.mjs"); }\n');
    assert.throws(() => checkBoundary(registration, { root: temporary }), /escape|missing import/);
    await cp(join(componentRoot, 'core.mjs'), join(temporary, 'core.mjs'));
    await appendFile(join(temporary, 'core.mjs'), '\nimport "./unlisted-helper.mjs";\n');
    await writeFile(join(temporary, 'unlisted-helper.mjs'), 'export async function escape() { return import("../../app/private.mjs"); }\n');
    assert.throws(() => checkBoundary(registration, { root: temporary }), /transitive source.*declared runtime/);
  } finally { await rm(temporary, { recursive: true, force: true }); }
});

test('qualifier entrypoint executes through symlinked paths and retains strict boundary reporting', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-qualifier-symlink-'));
  try {
    const alias = join(temporary, 'qualify.mjs');
    await symlink(join(componentRoot, 'qualify.mjs'), alias);
    const metadata = JSON.parse(await readFile(join(componentRoot, 'package.json'), 'utf8'));
    const registration = { artifact: { name: metadata.name, version: metadata.version }, dependencies: Object.keys(metadata.dependencies), publicExports: Object.keys(publicInterface) };
    const child = spawnSync(process.execPath, [alias, 'boundary'], { input: JSON.stringify(registration), encoding: 'utf8' });
    assert.equal(child.status, 0, child.stderr);
    const report = JSON.parse(child.stdout);
    assert.equal(report.success, true);
    assert(report.files > 0);
    assert.deepEqual(report.exports, Object.keys(publicInterface).sort());
  } finally { await rm(temporary, { recursive: true, force: true }); }
});
