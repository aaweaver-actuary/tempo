import test from 'node:test';
import assert from 'node:assert/strict';
import { collectionBlockers, operateCollection, serviceName } from '../../scripts/game-review-collection.mjs';
const revision = 'a'.repeat(40), imageId = 'sha256:' + 'b'.repeat(64);
function facts() {
  const configuration = { interval_seconds: 60, window: {start_at: '2026-10-29T10:00:00Z', end_at: '2026-10-29T11:00:00Z'} };
  return { candidate: revision, release: { revision, configFingerprint: 'frozen', images: {[serviceName]: imageId} }, fingerprint: 'frozen',
    target: {project: 'fixture'}, hostCommands: [], containers: [],
    service: { command: ['python', '-m', 'app.product_composition.game_sync_current', '--collect-only', '--follow', '--overlap-seconds', '300', '--reconcile-interval-seconds', '600'],
      environment: {TEMPO_PRODUCT_GAME_SYNC_ENABLED: '1'}, mem_limit: 268435456, pids_limit: 96,
      labels: {'tempo.collection.owner': 'managed-game-collection', 'tempo.collection.mode': 'collect-only'} },
    image: {Id: imageId, Config: {Labels: {'org.opencontainers.image.revision': revision}}},
    report: {schema_version: 1, read_only: true, identity: {availability: 'available'}, collection: {availability: 'available'}, delivery: {availability: 'available'},
      providers: {availability: 'available', items: [{provider: 'lichess', configured: true, configuration, required_inputs: {account: true},
        repertoire_readiness: [{availability: 'available', learner_color: 'white'}]}]}},
    preview: {providers: [{provider: 'lichess', configured: true, configuration}]} };
}
test('collection switch requires structured configuration and installed identity despite successful reads', () => {
  const input = facts(); assert.deepEqual(collectionBlockers(input), []);
  input.report.providers.items[0].configured = false;
  assert.ok(collectionBlockers(input).includes('collection_configuration_absent'));
  input.release.revision = 'c'.repeat(40);
  assert.ok(collectionBlockers(input).includes('installed_candidate_mismatch'));
});
test('collection switch refuses unknown inventory, delivery peers and changed frozen resources', () => {
  const input = facts(); input.hostCommands = null;
  assert.ok(collectionBlockers(input).includes('host_process_inventory_unavailable'));
  input.hostCommands = ['python -m app.product_composition.game_sync_current'];
  assert.ok(collectionBlockers(input).includes('host_collector_conflict'));
  input.service.cpus = 0.15;
  assert.ok(collectionBlockers(input).includes('collector_resource_or_ownership_mismatch'));
  input.containers = [{State: {Running: true}, Config: {Cmd: ['python', '-m', 'app.product_composition.game_sync_current'], Labels: {}}}];
  assert.ok(collectionBlockers(input).includes('competing_or_unverified_collector'));
});
test('collection switch refuses unavailable repertoire and overlapping seed', () => {
  const input = facts(); input.report.providers.items[0].repertoire_readiness[0].availability = 'unavailable';
  input.preview.providers[0].configuration.window.end_at = '2026-10-29T10:04:00Z';
  assert.ok(collectionBlockers(input).includes('lichess:repertoire_readiness_unresolved'));
  assert.ok(collectionBlockers(input).includes('lichess:invalid_forward_seed'));
});
test('plan and status are read-only; enable is explicit, locked, idempotent and blocked safely', async () => {
  const calls = []; let running = false, blockers = [];
  const ports = {inspect: async () => ({blockers, running, stopSafe: true, output: {running}}),
    lock: () => {calls.push('lock'); return () => calls.push('unlock');},
    start: async () => {calls.push('start'); running = true;}, stop: async () => {calls.push('stop'); running = false;}};
  await operateCollection('plan', ports); await operateCollection('status', ports); assert.deepEqual(calls, []);
  await assert.rejects(operateCollection('enable', ports), /--apply/); assert.deepEqual(calls, []);
  blockers = ['missing_scope']; await assert.rejects(operateCollection('enable', {...ports, apply: true}), /missing_scope/);
  assert.deepEqual(calls, ['lock', 'unlock']); calls.length = 0; blockers = [];
  await operateCollection('enable', {...ports, apply: true}); await operateCollection('enable', {...ports, apply: true});
  assert.deepEqual(calls, ['lock', 'start', 'unlock', 'lock', 'unlock']);
  await operateCollection('disable', {...ports, apply: true}); assert.equal(running, false);
});
test('disable refuses foreign ownership and releases the lifecycle lock on failure', async () => {
  let released = false;
  await assert.rejects(operateCollection('disable', {apply: true,
    inspect: async () => ({stopSafe: false, running: true}), lock: () => () => {released = true;},
    stop: () => assert.fail('foreign service stopped')}), /ownership/);
  assert.equal(released, true);
});
