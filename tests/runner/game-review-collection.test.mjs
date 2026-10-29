import test from 'node:test';
import assert from 'node:assert/strict';
import { collectionBlockers, operateCollection, serviceName } from '../../scripts/game-review-collection.mjs';
const revision = 'a'.repeat(40), imageId = 'sha256:' + 'b'.repeat(64);
function facts() {
  const configuration = { interval_seconds: 60, window: {start_at: '2026-10-29T10:00:00Z', end_at: '2026-10-29T11:00:00Z'} };
  return { candidate: revision, release: { revision, configFingerprint: 'frozen', images: {[serviceName]: imageId} }, fingerprint: 'frozen',
    target: {project: 'fixture', root: '/fixture'}, hostCommands: [], containers: [],
    service: { command: ['python', '-m', 'app.product_composition.game_sync_current', '--collect-only', '--follow', '--overlap-seconds', '300', '--reconcile-interval-seconds', '600'],
      environment: {TEMPO_PRODUCT_GAME_SYNC_ENABLED: '1', TEMPO_DATABASE_WRITE_URL: 'postgresql://tempo_writer@postgres:5432/tempo',
        TEMPO_DATABASE_READ_URL: 'postgresql://tempo_writer@postgres:5432/tempo', PGPASSFILE: '/run/secrets/worker_pgpass'},
      secrets: [{source: 'writer_pgpass', target: 'worker_pgpass'}], mem_limit: 268435456, pids_limit: 96,
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
  const enabled = await operateCollection('enable', {...ports, apply: true});
  assert.equal(enabled.running, true);
  await operateCollection('enable', {...ports, apply: true});
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


test('new managed releases include the inactive profile while frozen receipts remain exact', async t => {
  const {mkdtempSync,writeFileSync,rmSync} = await import('node:fs');
  const {tmpdir} = await import('node:os');
  const {join} = await import('node:path');
  const {releaseComposeFiles} = await import('../../scripts/tempo-runtime.mjs');
  const root = mkdtempSync(join(tmpdir(), 'tempo-g-profile-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const target = {root, composeFiles: [join(root, 'base.yml')]};
  assert.deepEqual(releaseComposeFiles(target), target.composeFiles);
  const overlay = join(root, 'docker-compose.games-feedback.yml'); writeFileSync(overlay, 'services: {}');
  assert.deepEqual(releaseComposeFiles(target), [...target.composeFiles, overlay]);
  const previous = {composeFiles: ['/frozen/old.yml']};
  assert.deepEqual(releaseComposeFiles(target, {fallback: true, previous}), previous.composeFiles);
  assert.deepEqual(releaseComposeFiles({...target, composeFiles: [...target.composeFiles, overlay]}), [...target.composeFiles, overlay]);
});
test('empty launch inputs can be packaged inactive but cannot enable collection', () => {
  const input = facts(); input.service.command[input.service.command.indexOf('--overlap-seconds') + 1] = '';
  assert.ok(collectionBlockers(input).includes('explicit_overlap_seconds_required'));
  input.service.environment.TEMPO_DATABASE_WRITE_URL = 'postgresql://another-database';
  assert.ok(collectionBlockers(input).includes('collector_database_role_or_secret_mismatch'));
});

test('managed maintenance stops Games but application startup and readiness never require it', async t => {
  const {readFileSync} = await import('node:fs');
  const {join} = await import('node:path');
  const {cliFixture} = await import('./tempo-cli-fixture.mjs');
  const {createRuntime, applicationServices} = await import('../../scripts/tempo-runtime.mjs');
  const fixture = cliFixture();
  const {rmSync} = await import('node:fs');
  t.after(() => rmSync(fixture.directory, {recursive: true, force: true}));
  const configuration = JSON.parse(readFileSync(join(fixture.directory, 'fixture.json'), 'utf8')).config;
  configuration.services[serviceName] = facts().service;
  const calls = [];
  const runtime = createRuntime(fixture.target, {revision, stateDirectory: fixture.stateDirectory, log: () => {},
    run: async (_program, args) => {
      calls.push(args);
      return {stdout: JSON.stringify(args.includes('config') ? configuration : args.includes('ps') ? [{State: 'running', Service: serviceName}] : {})};
    }});
  await runtime.config();
  await runtime.stopApplications();
  assert.ok(calls.find(args => args.includes('stop')).includes(serviceName));
  await runtime.startServices();
  assert.ok(calls.filter(args => args.includes('up')).length > 0);
  assert.ok(calls.filter(args => args.includes('up')).every(args => !args.includes(serviceName)));
  assert.ok(!applicationServices.includes(serviceName));
});

test('CLI builder fixture reports the requested unrestricted CPU and original memory policy', async t => {
  const {cliFixture} = await import('./tempo-cli-fixture.mjs');
  const {spawnSync} = await import('node:child_process');
  const {join} = await import('node:path');
  const {rmSync} = await import('node:fs');
  const fixture = cliFixture(); t.after(() => rmSync(fixture.directory, {recursive: true, force: true}));
  const docker = args => {
    const result = spawnSync(join(fixture.directory, 'bin/docker'), args, {encoding: 'utf8', env: fixture.environment});
    assert.equal(result.status, 0, result.stderr); return result.stdout;
  };
  docker(['buildx', 'create', '--name', 'fixture-builder', '--driver-opt', 'memory=2048m', '--driver-opt', 'env.TEMPO_RESOURCE_OWNER=fixture']);
  docker(['buildx', 'inspect', 'fixture-builder', '--bootstrap']);
  const [container] = JSON.parse(docker(['inspect', 'buildx_buildkit_fixture-builder0']));
  assert.equal(container.HostConfig.CpuQuota, 0); assert.equal(container.HostConfig.NanoCpus, 0);
  assert.equal(container.HostConfig.Memory, 2048 * 1024 * 1024);
});

test('only the exact owned collect-only command may coexist, including stopped delivery-capable peers', () => {
  const input = facts();
  const container = {Image: imageId, State: {Running: true}, HostConfig: {Memory: 268435456, PidsLimit: 96, NanoCpus: 0, CpuQuota: 0},
    Config: {Cmd: [...input.service.command], Labels: {'com.docker.compose.project': 'fixture', 'com.docker.compose.service': serviceName, 'com.docker.compose.project.working_dir': '/fixture',
      'tempo.collection.owner': 'managed-game-collection', 'tempo.collection.mode': 'collect-only'}}};
  input.containers = [container]; assert.deepEqual(collectionBlockers(input), []);
  container.Config.Cmd[container.Config.Cmd.indexOf('--overlap-seconds') + 1] = '1';
  assert.ok(collectionBlockers(input).includes('competing_or_unverified_collector'));
  container.State.Running = false; container.Image = 'sha256:' + 'c'.repeat(64);
  assert.deepEqual(collectionBlockers(input), [], 'an owned stopped collect-only service can be recreated for the new release');
  container.Config.Cmd = ['python', '-m', 'app.product_composition.game_sync_current'];
  assert.ok(collectionBlockers(input).includes('competing_or_unverified_collector'));
});
