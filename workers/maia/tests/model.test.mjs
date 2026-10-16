import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createModel, descriptorModelVersion, inspectModel } from '../model.mjs';

const directory = fileURLToPath(new URL('../assets/', import.meta.url));
const paths = { model: join(directory, 'model.onnx'), moveIndex: join(directory, 'move-index.json'), reverseIndex: join(directory, 'reverse-index.json') };
const descriptor = JSON.parse(await readFile(new URL('../model-descriptor.json', import.meta.url), 'utf8'));

test('maia_installed_model_identity_verifies_real_assets_runtime_and_unknown_provenance', async () => {
  const identity = await inspectModel();
  assert.equal(identity.model_id, 'maia3-onnx');
  assert.equal(identity.model_version, descriptorModelVersion(descriptor));
  assert.equal(identity.runtime_name, 'onnxruntime-web/wasm');
  assert.equal(identity.runtime_version, '1.23.0');
  assert.equal(identity.chess_library_version, '1.4.0');
  assert.equal(identity.checksums.model_sha256, '405bf76c15727dad8728b352c06a8f3c1b80fb2760e8d666b32485c63d75b856');
  assert.equal(identity.descriptor.upstream_checkpoint, null);
  assert.equal(identity.descriptor.upstream_export_revision, null);
  assert.equal(identity.runtime_context.node, process.version);
  assert.equal(identity.runtime_context.architecture, process.arch);
  for (const [name, hash] of Object.entries(identity.implementation_files)) assert.equal(hash, createHash('sha256').update(await readFile(new URL(`../${name}`, import.meta.url))).digest('hex'));
  assert.ok(Object.isFrozen(identity) && Object.isFrozen(identity.descriptor));
});

test('maia_model_version_binds_model_vocabularies_normalization_input_and_runtime_descriptor', () => {
  for (const field of ['model_sha256', 'move_index_sha256', 'reverse_index_sha256', 'inference_schema', 'runtime_version', 'chess_library_version', 'normalization', 'upstream_checkpoint']) {
    assert.notEqual(descriptorModelVersion({ ...descriptor, [field]: 'changed' }), descriptor.model_version, field);
  }
  assert.notEqual(descriptorModelVersion({ ...descriptor, supported_input_shape: { tokens: [2, 64, 12] } }), descriptor.model_version);
  assert.equal(descriptorModelVersion({ ...descriptor, source_assets: {} }), descriptor.model_version);
});

test('maia_missing_model_asset_is_distinct_from_checksum_failure', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-missing-'));
  try { await assert.rejects(inspectModel({ assetsDirectory: temporary }), (error) => error.code === 'model_asset_missing' && error.retryable === false); }
  finally { await rm(temporary, { recursive: true, force: true }); }
});

test('maia_changed_model_hash_fails_before_runtime_session_creation', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-model-hash-'));
  try {
    const changedModel = join(temporary, 'changed.onnx');
    await writeFile(changedModel, 'changed model bytes');
    await assert.rejects(createModel({ assetPaths: { ...paths, model: changedModel } }), (error) => error.code === 'model_hash_mismatch' && error.retryable === false);
  } finally { await rm(temporary, { recursive: true, force: true }); }
});

for (const field of ['moveIndex', 'reverseIndex']) test(`maia_wrong_${field}_checksum_is_explicitly_rejected`, async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'tempo-maia-vocabulary-hash-'));
  try {
    const changedVocabulary = join(temporary, 'changed.json');
    await writeFile(changedVocabulary, '{}');
    await assert.rejects(inspectModel({ assetPaths: { ...paths, [field]: changedVocabulary } }), (error) => error.code === 'vocabulary_hash_mismatch' && error.retryable === false);
  } finally { await rm(temporary, { recursive: true, force: true }); }
});
