import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { assessSelectedReadiness, collectorMode, serviceInMode, readProbe, collectionBlockers, main, operateCollection, readCollectionMaintenance, serviceName } from '../../scripts/game-review-collection.mjs';
import { targetKey } from '../../scripts/tempo-deployment.mjs';
import { configurationFingerprint } from '../../scripts/tempo-runtime.mjs';
import { cliFixture } from './tempo-cli-fixture.mjs';
const revision = 'a'.repeat(40), imageId = 'sha256:' + 'b'.repeat(64);
function facts() {
  const identity = {availability: 'available', schema_version: 54, reader_role: 'tempo_reader', transaction_read_only: 'on'};
  const configuration = { interval_seconds: 60, window: {start_at: '2026-10-29T10:00:00Z', end_at: '2026-10-29T11:00:00Z'} };
  return { candidate: revision, release: { revision, schema: 54, configFingerprint: 'frozen', images: {[serviceName]: imageId} }, fingerprint: 'frozen',
    maintenance: {status: 'clear'},
    identityProbe: {status: 'success', json_status: 'valid', evidence: {schema_version: 1, read_only: true, identity}},
    fullReaderProbe: {status: 'success', json_status: 'valid'},
    target: {project: 'fixture', root: '/fixture'}, hostCommands: [], containers: [],
    service: { command: ['python', '-m', 'app.product_composition.game_sync_current', '--collect-only', '--follow', '--overlap-seconds', '300', '--reconcile-interval-seconds', '600'],
      environment: {TEMPO_PRODUCT_GAME_SYNC_ENABLED: '1', TEMPO_DATABASE_WRITE_URL: 'postgresql://tempo_writer@postgres:5432/tempo',
        TEMPO_DATABASE_READ_URL: 'postgresql://tempo_writer@postgres:5432/tempo', PGPASSFILE: '/run/secrets/worker_pgpass'},
      secrets: [{source: 'writer_pgpass', target: 'worker_pgpass'}], mem_limit: 536870912, pids_limit: 96,
      labels: {'tempo.collection.owner': 'managed-game-collection', 'tempo.collection.mode': 'collect-only'} },
    image: {Id: imageId, Config: {Labels: {'org.opencontainers.image.revision': revision}}},
    report: {schema_version: 1, read_only: true, identity, collection: {availability: 'available'}, delivery: {availability: 'available'},
      providers: {availability: 'available', items: [{provider: 'lichess', configured: true, configuration, required_inputs: {account: true},
        selected_repertoire_count: 1, repertoires_truncated: false,
        repertoire_readiness: [{repertoire_id: 'redacted:111111111111', availability: 'available', learner_color: 'white'}]},
        {provider: 'chesscom', configured: false}]}},
    preview: {providers: [{provider: 'lichess', configured: true, configuration}]} };
}
test('collection switch requires structured configuration and installed identity despite successful reads', () => {
  const input = facts(); assert.deepEqual(collectionBlockers(input), []);
  input.report.providers.items[0].configured = false;
  assert.ok(collectionBlockers(input).includes('collection_configuration_absent'));
  input.release.revision = 'c'.repeat(40);
  assert.ok(collectionBlockers(input).includes('installed_candidate_mismatch'));
});
test('all-repertoire collection requires its calibrated 512 MiB worker allowance', () => {
  const input = facts();
  assert.equal(input.service.mem_limit, 512 * 1024 * 1024);
  assert.deepEqual(collectionBlockers(input), []);
  input.service.mem_limit = 256 * 1024 * 1024;
  assert.ok(collectionBlockers(input).includes('collector_resource_or_ownership_mismatch'));
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
  const container = {Image: imageId, State: {Running: true}, HostConfig: {Memory: 536870912, PidsLimit: 96, NanoCpus: 0, CpuQuota: 0},
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

test('collection enable blocks pending migration', () => {
  const input = facts();
  input.release.schema = 54;
  input.report.identity.schema_version = 54;
  input.maintenance = {status: 'blocked', code: 'migration_pending'};
  assert.ok(collectionBlockers(input).includes('migration_pending'));
});

test('collection enable blocks schema mismatch', () => {
  const input = facts();
  input.release.schema = 54;
  input.report.identity.schema_version = 53;
  input.maintenance = {status: 'clear'};
  assert.ok(collectionBlockers(input).includes('collection_schema_mismatch'));
});

function maintenanceFixture(t) {
  const stateDirectory = mkdtempSync(join(tmpdir(), 'tempo-collection-maintenance-'));
  t.after(() => rmSync(stateDirectory, {recursive: true, force: true}));
  const target = {daemonId: 'fixture-daemon', context: 'fixture-context', postgresVolumeKey: 'fixture-postgres',
    volumes: {'fixture-postgres': {name: 'fixture-only-postgres'}}};
  const configuration = {services: {postgres: {environment: {POSTGRES_DB: 'tempo'}}}};
  return {stateDirectory, target, configuration};
}

function verifiedGuard(target) {
  return {version: 1, target: targetKey(target),
    database: {name: 'tempo', volume: target.volumes[target.postgresVolumeKey].name},
    state: 'verified', starting_schema: 54, intended_schema: 54,
    starting_versions: Array.from({length: 54}, (_, index) => index + 1),
    study_invariants: {fixture: true}, backup: {verified: true, filename: 'fixture-only.dump'}};
}

function maintenanceBytes(stateDirectory) {
  return Object.fromEntries(['migration-guard.json', 'operation.json'].map(filename => {
    const path = join(stateDirectory, filename);
    return [filename, existsSync(path) ? readFileSync(path) : null];
  }));
}

test('collection maintenance assessment preserves existing recovery policy', async t => {
  const cases = [
    {name: 'both files absent'},
    {name: 'pending guard', guard: target => ({...verifiedGuard(target), state: 'pending'}), code: 'migration_pending'},
    {name: 'verified guard', guard: verifiedGuard},
    {name: 'foreign target', guard: target => ({...verifiedGuard(target), target: 'another-target'}), code: 'migration_guard_invalid'},
    {name: 'foreign database', guard: target => ({...verifiedGuard(target), database: {name: 'other', volume: target.volumes[target.postgresVolumeKey].name}}), code: 'migration_guard_invalid'},
    {name: 'foreign volume', guard: target => ({...verifiedGuard(target), database: {name: 'tempo', volume: 'another-volume'}}), code: 'migration_guard_invalid'},
    {name: 'empty guard object', guard: () => ({}), code: 'migration_guard_invalid'},
    {name: 'applying migration without guard', operation: {phase: 'applying_migrations'}, code: 'migration_guard_missing'},
    {name: 'failed migration without guard', operation: {phase: 'failed', failed_phase: 'applying_migrations'}, code: 'migration_guard_missing'},
    {name: 'completed operation without guard', operation: {phase: 'complete'}},
    {name: 'verified guard overrides stale failed migration', guard: verifiedGuard,
      operation: {phase: 'failed', failed_phase: 'applying_migrations'}, forbidOperationRead: true},
  ];
  for (const entry of cases) await t.test(entry.name, t => {
    const fixture = maintenanceFixture(t);
    if (entry.guard) writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), JSON.stringify(entry.guard(fixture.target)));
    if (entry.operation) writeFileSync(join(fixture.stateDirectory, 'operation.json'), JSON.stringify(entry.operation));
    const before = maintenanceBytes(fixture.stateDirectory), reads = [];
    const assessment = readCollectionMaintenance({...fixture, readFile: (path, encoding) => {
      reads.push(basename(path));
      if (entry.forbidOperationRead && basename(path) === 'operation.json') assert.fail('stale operation must not be read');
      return readFileSync(path, encoding);
    }});
    assert.deepEqual(assessment, entry.code ? {status: 'blocked', code: entry.code} : {status: 'clear'});
    assert.deepEqual(reads, entry.guard ? ['migration-guard.json'] : ['migration-guard.json', 'operation.json']);
    assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
  });
});

test('collection maintenance evidence errors block enable without destructive recovery', async t => {
  const cases = [
    ...['', '{invalid', 'null', '[]', '"guard"', '54', 'true'].map(text => ({filename: 'migration-guard.json', text})),
    {filename: 'migration-guard.json', error: 'EACCES'},
    ...['{invalid', 'null'].map(text => ({filename: 'operation.json', text})),
    {filename: 'operation.json', error: 'EIO'},
  ];
  for (const entry of cases) await t.test(`${entry.filename}: ${entry.error ?? JSON.stringify(entry.text)}`, t => {
    const fixture = maintenanceFixture(t);
    writeFileSync(join(fixture.stateDirectory, entry.filename), entry.text ?? '{"fixture":"preserve"}');
    const before = maintenanceBytes(fixture.stateDirectory);
    let assessment;
    assert.doesNotThrow(() => {
      assessment = readCollectionMaintenance({...fixture, readFile: (path, encoding) => {
        if (entry.error && basename(path) === entry.filename) throw Object.assign(new Error('private fixture error'), {code: entry.error});
        return readFileSync(path, encoding);
      }});
    });
    assert.deepEqual(assessment, {status: 'blocked', code: 'collection_maintenance_unavailable'});
    assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
    const input = facts(); input.maintenance = assessment;
    assert.ok(collectionBlockers(input).includes('collection_maintenance_unavailable'));
  });
});

test('collection enable requires exact positive integer schema evidence', async t => {
  for (const observed of [54, 53, 55]) await t.test(`observed schema ${observed}`, () => {
    const input = facts(); input.report.identity.schema_version = observed;
    assert.deepEqual(collectionBlockers(input), observed === 54 ? [] : ['collection_schema_mismatch']);
    assert.equal(input.report.schema_version, 1);
  });
  for (const field of ['expected', 'observed']) {
    for (const value of [undefined, null, 0, -1, 54.5, '54', NaN, Infinity, -Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      await t.test(`${field} schema ${typeof value}:${String(value)}`, () => {
        const input = facts();
        if (field === 'expected') input.release.schema = value;
        else input.report.identity.schema_version = value;
        assert.deepEqual(collectionBlockers(input), field === 'observed'
          ? ['full_reader_identity_invalid', 'collection_schema_unverified'] : ['collection_schema_unverified']);
      });
    }
  }
  await t.test('unavailable identity despite numeric schema', () => {
    const input = facts(); input.report.identity.availability = 'unavailable';
    assert.ok(collectionBlockers(input).includes('collection_schema_unverified'));
  });
  await t.test('omitted maintenance evidence', () => {
    const input = facts(); delete input.maintenance;
    assert.deepEqual(collectionBlockers(input), ['collection_maintenance_unavailable']);
  });
});

test('collection enable assesses maintenance after acquiring the target lock', async t => {
  const fixture = maintenanceFixture(t);
  assert.deepEqual(readCollectionMaintenance(fixture), {status: 'clear'});
  let starts = 0, releases = 0, locked = false;
  await assert.rejects(operateCollection('enable', {apply: true,
    lock: () => {
      locked = true;
      writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), JSON.stringify({...verifiedGuard(fixture.target), state: 'pending'}));
      return () => {locked = false; releases++;};
    },
    inspect: async () => {
      assert.equal(locked, true);
      const input = facts(); input.maintenance = readCollectionMaintenance(fixture);
      return {blockers: collectionBlockers(input), running: false, stopSafe: true, output: {running: false}};
    }, start: () => {starts++;}, stop: () => assert.fail('unexpected stop'),
  }), /migration_pending/);
  assert.equal(starts, 0); assert.equal(releases, 1); assert.equal(locked, false);
});

test('collection disable remains available with maintenance and schema launch blockers', async t => {
  const cases = [
    {name: 'pending migration', guard: target => JSON.stringify({...verifiedGuard(target), state: 'pending'}), code: 'migration_pending'},
    {name: 'invalid maintenance JSON', guard: () => '{invalid', code: 'collection_maintenance_unavailable'},
    {name: 'schema mismatch', observed: 53, code: 'collection_schema_mismatch'},
    {name: 'unavailable observed schema', observed: undefined, code: 'collection_schema_unverified'},
  ];
  for (const entry of cases) await t.test(entry.name, async t => {
    const fixture = maintenanceFixture(t);
    if (entry.guard) writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), entry.guard(fixture.target));
    writeFileSync(join(fixture.stateDirectory, 'operation.json'), '{"phase":"complete","fixture":"preserve"}');
    const before = maintenanceBytes(fixture.stateDirectory);
    let running = true, starts = 0, stops = 0, releases = 0, locked = false;
    const ports = {apply: true,
      lock: () => {locked = true; return () => {locked = false; releases++;};},
      inspect: async () => {
        assert.equal(locked, true);
        const input = facts(); input.maintenance = readCollectionMaintenance(fixture);
        if (Object.hasOwn(entry, 'observed')) input.report.identity.schema_version = entry.observed;
        return {blockers: collectionBlockers(input), running, stopSafe: true, output: {running}};
      }, start: () => {starts++;}, stop: () => {stops++; running = false;},
    };
    await assert.rejects(operateCollection('enable', ports), new RegExp(entry.code));
    assert.equal(releases, 1);
    const output = await operateCollection('disable', ports);
    assert.equal(output.running, false); assert.equal(output.changed, true);
    assert.equal(starts, 0); assert.equal(stops, 1); assert.equal(releases, 2); assert.equal(locked, false);
    assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
  });
});

function collectionMainFixture(t, {running = false, modern = false, observedSchema = modern ? 56 : 54} = {}) {
  const fixture = cliFixture();
  t.after(() => rmSync(fixture.directory, {recursive: true, force: true}));
  const configuration = JSON.parse(readFileSync(join(fixture.directory, 'fixture.json'), 'utf8')).config;
  const input = facts();
  configuration.services[serviceName] = input.service;
  const apiImage = 'sha256:' + 'c'.repeat(64);
  configuration.services.api.image = apiImage;
  if (modern) {
    configuration.services['foreground-worker'] = {...configuration.services['foreground-worker'], image: apiImage};
    input.report.feedback_capabilities = {availability: 'available', policy_version: 'first_departure_v1', receipt_interface_version: '0.2.2',
      selector: true, current_authority: true, again: true, canonical_introduction: true, retained_qualification: true,
      foreground_handoff: true, issuance_fence: true};
    input.report.managed_mode = {availability: 'available', intent: null};
  }
  configuration.services[serviceName].image = imageId;
  fixture.target.envFile = join(fixture.directory, 'fixture.env');
  writeFileSync(fixture.target.envFile, '');
  writeFileSync(fixture.registration, JSON.stringify(fixture.target));
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(configuration));
  const imageOverride = join(fixture.directory, 'recorded-images.json');
  writeFileSync(imageOverride, JSON.stringify({services: {api: {image: apiImage}, [serviceName]: {image: imageId}}}));
  writeFileSync(join(fixture.stateDirectory, 'deployment.json'), JSON.stringify({version: 1, revision, schema: modern ? 56 : 54,
    images: {api: apiImage, [serviceName]: imageId, ...(modern ? {'foreground-worker': apiImage} : {})}, composeFiles: fixture.target.composeFiles, imageOverride,
    ...(modern ? {evidence: {commit: revision, run_id: 7, url: 'https://github.com/aaweaver-actuary/tempo/actions/runs/7'}} : {}),
    configFingerprint: configurationFingerprint(configuration)}));
  input.report.identity.schema_version = observedSchema;
  const api = {Id: 'fixture-api', Image: apiImage, State: {Running: true, Restarting: false},
    Config: {Cmd: ['fixture-api'], Labels: {'com.docker.compose.project': fixture.target.project,
      'com.docker.compose.service': 'api', 'com.docker.compose.project.working_dir': fixture.target.root}}, Mounts: []};
  const collector = {Id: 'fixture-collector', Image: imageId, State: {Running: running, Restarting: false},
    HostConfig: {Memory: 536870912, PidsLimit: 96, NanoCpus: 0, CpuQuota: 0},
    Config: {Cmd: [...input.service.command], Labels: {...input.service.labels,
      'com.docker.compose.project': fixture.target.project, 'com.docker.compose.service': serviceName,
      'com.docker.compose.project.working_dir': fixture.target.root}}, Mounts: []};
  const foreground = {Id: 'b'.repeat(64), Image: apiImage, State: {Running: true},
    Config: {Hostname: 'b'.repeat(12), Cmd: ['fixture-foreground'], Labels: {'com.docker.compose.project': fixture.target.project,
      'com.docker.compose.service': 'foreground-worker', 'com.docker.compose.project.working_dir': fixture.target.root}}, Mounts: []};
  if (modern) {
    api.Id = 'a'.repeat(64); api.Config.Hostname = api.Id.slice(0, 12);
    collector.Id = 'c'.repeat(64); collector.Config.Hostname = collector.Id.slice(0, 12);
  }
  const runtimeBoots = {api: '1'.repeat(32), 'foreground-worker': '2'.repeat(32), [serviceName]: '3'.repeat(32)};
  let modeIntent = null, collectorBoots = 3;
  const inventory = modern ? [api, foreground, collector] : [api, collector];
  const calls = [], outputs = [], lockObservations = [], unexpected = [];
  const previousStateRoot = process.env.TEMPO_CLI_STATE_DIR;
  process.env.TEMPO_CLI_STATE_DIR = fixture.environment.TEMPO_CLI_STATE_DIR;
  t.after(() => {
    if (previousStateRoot === undefined) delete process.env.TEMPO_CLI_STATE_DIR;
    else process.env.TEMPO_CLI_STATE_DIR = previousStateRoot;
  });
  t.mock.method(console, 'log', text => outputs.push(JSON.parse(text)));
  const same = (actual, expected) => JSON.stringify(actual) === JSON.stringify(expected);
  const composePrefix = ['compose', '--project-directory', fixture.target.root, '--project-name', fixture.target.project,
    '--env-file', fixture.target.envFile, ...fixture.target.composeFiles.flatMap(file => ['-f', file]), '-f', imageOverride,
    '--profile', 'maintenance', '--profile', 'games-feedback'];
  const runCommand = (program, args, settings = {}) => {
    calls.push({program, args: [...args], settings});
    if (program === 'ps' && same(args, ['-axo', 'command='])) return '';
    if (program === 'docker' && same(args.slice(0, 2), ['--context', fixture.target.context])) {
      const action = args.slice(2);
      if (same(action, ['info', '--format', '{{.ID}}'])) {
        lockObservations.push(existsSync(join(fixture.stateDirectory, 'maintenance.lock')));
        return fixture.target.daemonId;
      }
      if (same(action, ['ps', '-aq'])) return inventory.map(container => container.Id).join('\n');
      if (same(action, ['inspect', ...inventory.map(container => container.Id)])) return JSON.stringify(inventory);
      if (same(action, ['image', 'inspect', imageId]) || same(action, ['image', 'inspect', apiImage])) {
        return JSON.stringify([{Id: action[2], Config: {Labels: {'org.opencontainers.image.revision': action[2] === apiImage ? input.apiRevision ?? revision : revision}}}]);
      }
      if (same(action.slice(0, composePrefix.length), composePrefix)) {
        let composeAction = action.slice(composePrefix.length);
        const effective = structuredClone(configuration);
        if (composeAction[0] === '-f') {
          const override = JSON.parse(readFileSync(composeAction[1], 'utf8'));
          const patch = override.services[serviceName];
          effective.services[serviceName] = {...effective.services[serviceName], command: patch.command,
            labels: {...effective.services[serviceName].labels, ...patch.labels}};
          composeAction = composeAction.slice(2);
        }
        if (same(composeAction, ['config', '--format', 'json'])) return JSON.stringify(effective);
        if (modern && same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_feedback_mode', 'status', '--workspace', 'legacy'])) {
          return JSON.stringify({schema_version: 1, read_only: true, intent: modeIntent});
        }
        if (modern && composeAction[0] === 'run' && composeAction.includes('app.product_composition.game_feedback_mode')) {
          assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), true);
          if (input.failModeRecord) throw new Error('Synthetic mode record failed');
          const payload = JSON.parse(composeAction[composeAction.indexOf('--intent-json') + 1]);
          const generation = Number(composeAction[composeAction.indexOf('--expected-generation') + 1]);
          assert.equal(generation, modeIntent?.generation ?? 0);
          modeIntent = payload; input.report.managed_mode.intent = payload;
          if (input.loseModeAcknowledgment) throw new Error('Synthetic acknowledgment lost after commit');
          return JSON.stringify({schema_version: 1, applied: true, generation: payload.generation, mode: payload.mode,
            phase: payload.phase, operation_id: payload.operation_id});
        }
        if (modern && composeAction[0] === 'exec' && composeAction.includes('app.product_composition.managed_runtime')) {
          const name = composeAction[2];
          if (input.failRuntime === name) throw new Error('Synthetic runtime identity unavailable');
          const container = inventory.find(container => container.Config.Labels['com.docker.compose.service'] === name);
          return JSON.stringify({schema_version: 1, read_only: true, runtime: {schema_version: 1,
            hostname: container.Config.Hostname, boot_nonce: runtimeBoots[name]}});
        }
        if (same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_collection_status', '--identity-only'])) {
          if (input.identityRead) return input.identityRead();
          return JSON.stringify(input.identityProbe.evidence);
        }
        if (same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_collection_status', '--workspace', 'legacy', '--limit', '20'])) {
          if (input.fullRead) return input.fullRead();
          return JSON.stringify(input.report);
        }
        if (same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_sync_current', '--preview', '--follow', '--overlap-seconds', '300'])) {
          return JSON.stringify(input.preview);
        }
        if (same(composeAction, ['up', '-d', '--no-build', '--no-deps', '--pull', 'never', '--wait', '--wait-timeout', '30', serviceName])) {
          assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), true);
          collector.State.Running = true;
          if (modern) {
            collector.Config.Cmd = [...effective.services[serviceName].command];
            collector.Config.Labels['tempo.collection.mode'] = effective.services[serviceName].labels['tempo.collection.mode'];
            runtimeBoots[serviceName] = (++collectorBoots).toString(16).padStart(32, '0');
          }
          return '';
        }
        if (same(composeAction, ['stop', '--timeout', '30', serviceName])) {
          assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), true);
          collector.State.Running = false;
          return '';
        }
      }
    }
    unexpected.push({program, args});
    throw new Error('Unexpected fixture command: ' + JSON.stringify({program, args}));
  };
  const actionCalls = action => calls.filter(call => call.program === 'docker' && call.args[2] === 'compose' && call.args.includes(action));
  const invoke = async (operation, {apply = true, allowances = [], mode} = {}) => {
    try {
      await main([operation, '--config', fixture.registration, '--candidate', revision,
        ...(['enable', 'disable'].includes(operation) && apply ? ['--apply'] : []),
        ...(mode ? ['--mode', mode] : []),
        ...allowances.flatMap(token => ['--allow-bound-repertoire', token])], {runCommand});
      return outputs.at(-1);
    } finally {
      assert.deepEqual(unexpected, [], 'fixture must recognize every external command');
      assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), false);
    }
  };
  return {...fixture, invoke, actionCalls, calls, lockObservations, configuration, input, inventory, runtimeBoots};
}

test('managed collector accepts only its two explicit finite command modes', () => {
  const original = facts().service;
  assert.equal(collectorMode(original.command), 'collect-only');
  assert.equal(collectorMode(serviceInMode(original, 'feedback').command), 'feedback');
  assert.equal(collectorMode(original.command.filter(value => value !== '--collect-only')), null);
  assert.equal(collectorMode([...original.command, '--feedback']), null);
  assert.equal(collectorMode([...original.command, '--once']), null);
  assert.equal(collectorMode([...original.command, '--follow']), null);
});

test('feedback readiness requires complete declared capabilities and exact qualified release evidence', () => {
  const input = facts();
  input.mode = 'feedback'; input.service = serviceInMode(input.service, 'feedback');
  input.release.schema = 56; input.report.identity.schema_version = 56;
  input.report.managed_mode = {availability: 'available', intent: null};
  input.report.feedback_capabilities = {availability: 'available', policy_version: 'first_departure_v1', receipt_interface_version: '0.2.2',
    selector: true, current_authority: true, again: true, canonical_introduction: true, retained_qualification: true,
    foreground_handoff: true, issuance_fence: true};
  input.release.evidence = {commit: revision, run_id: 7, url: 'https://github.com/aaweaver-actuary/tempo/actions/runs/7'};
  assert.deepEqual(collectionBlockers(input), []);
  input.report.feedback_capabilities.again = false;
  assert.ok(collectionBlockers(input).includes('feedback_capabilities_unverified'));
  input.report.feedback_capabilities.again = true;
  input.release.evidence.commit = 'c'.repeat(40);
  assert.ok(collectionBlockers(input).includes('feedback_qualified_release_evidence_required'));
  input.release.evidence.commit = revision;
  input.report.managed_mode.availability = 'unavailable';
  assert.ok(collectionBlockers(input).includes('managed_feedback_mode_bridge_unavailable'));
  input.report.managed_mode.availability = 'available';
  partialScope(input);
  input.allowances = ['redacted:222222222222'];
  assert.ok(collectionBlockers(input).includes('bound_allowance_collection_only'));
  assert.ok(collectionBlockers(input).includes('lichess:repertoire_readiness_unresolved'));
});

test('feedback plan and status never change mode intent configuration or running services', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  const before = readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8');
  const plan = await fixture.invoke('plan', {mode: 'feedback'});
  assert.equal(plan.mode, 'feedback'); assert.equal(plan.running, false); assert.equal(plan.activation_ready, true);
  await fixture.invoke('status', {mode: 'feedback'});
  assert.equal(fixture.actionCalls('run').length, 0); assert.equal(fixture.actionCalls('up').length, 0);
  assert.equal(fixture.actionCalls('stop').length, 0);
  assert.equal(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'), before);
});

test('feedback accepts the exact installer no-ci receipt while preserving activation guards', async t => {
  const input = facts();
  input.mode = 'feedback'; input.service = serviceInMode(input.service, 'feedback');
  input.release.schema = 56; input.report.identity.schema_version = 56;
  input.report.managed_mode = {availability: 'available', intent: null};
  input.report.feedback_capabilities = {availability: 'available', policy_version: 'first_departure_v1', receipt_interface_version: '0.2.2',
    selector: true, current_authority: true, again: true, canonical_introduction: true, retained_qualification: true,
    foreground_handoff: true, issuance_fence: true};
  input.release.evidence = {commit: revision, verification: 'ci-disabled'};
  assert.deepEqual(collectionBlockers(input), []);
  for (const evidence of [null, {}, {commit: 'c'.repeat(40), verification: 'ci-disabled'},
    {commit: revision, verification: 'unavailable'}, {commit: revision},
    {commit: revision, verification: 'ci-disabled', run_id: 7}]) {
    const invalid = structuredClone(input); invalid.release.evidence = evidence;
    assert.ok(collectionBlockers(invalid).includes('feedback_qualified_release_evidence_required'));
  }
  for (const [mutate, blocker] of [
    [value => { value.report.feedback_capabilities.again = false; }, 'feedback_capabilities_unverified'],
    [value => { value.report.managed_mode.availability = 'unavailable'; }, 'managed_feedback_mode_bridge_unavailable'],
    [value => { value.report.identity.schema_version = 54; }, 'collection_schema_mismatch'],
    [value => { value.image.Config.Labels['org.opencontainers.image.revision'] = 'c'.repeat(40); }, 'collector_image_unverified'],
    [value => { value.fingerprint = 'changed'; }, 'frozen_configuration_mismatch'],
    [value => { value.maintenance.status = 'pending'; }, 'collection_maintenance_unavailable'],
    [value => { value.service.labels['tempo.collection.owner'] = 'unverified'; }, 'collector_resource_or_ownership_mismatch'],
    [value => { partialScope(value); }, 'lichess:repertoire_readiness_unresolved'],
  ]) {
    const invalid = structuredClone(input); mutate(invalid);
    assert.ok(collectionBlockers(invalid).includes(blocker), blocker);
  }
  const fixture = collectionMainFixture(t, {modern: true});
  const receipt = JSON.parse(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'));
  receipt.evidence = input.release.evidence;
  writeFileSync(join(fixture.stateDirectory, 'deployment.json'), JSON.stringify(receipt));
  const plan = await fixture.invoke('plan', {mode: 'feedback'});
  assert.equal(plan.activation_ready, true);
  assert.deepEqual(plan.release_verification, {commit: revision, verification: 'ci-disabled'});
  assert.equal(fixture.actionCalls('run').length, 0); assert.equal(fixture.actionCalls('up').length, 0);
});

test('managed feedback enables idempotently and changes mode only after the previous worker stops', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  const enabled = await fixture.invoke('enable', {mode: 'feedback'});
  assert.equal(enabled.observed_mode, 'feedback'); assert.equal(enabled.managed_mode.verified_for_current_runtime, true);
  assert.equal(enabled.managed_mode.generation, 2);
  const starts = fixture.actionCalls('up').length, records = fixture.actionCalls('run').length;
  const duplicate = await fixture.invoke('enable', {mode: 'feedback'});
  assert.equal(duplicate.changed, false);
  assert.equal(fixture.actionCalls('up').length, starts); assert.equal(fixture.actionCalls('run').length, records);
  const collecting = await fixture.invoke('enable', {mode: 'collect-only'});
  assert.equal(collecting.observed_mode, 'collect-only'); assert.equal(collecting.managed_mode.verified_for_current_runtime, true);
  const actions = fixture.calls.filter(call => ['stop', 'up'].some(action => call.args.includes(action)));
  assert.deepEqual(actions.map(call => call.args.includes('stop') ? 'stop' : 'up'), ['up', 'stop', 'up']);
  const disabled = await fixture.invoke('disable', {mode: 'collect-only'});
  assert.equal(disabled.running, false); assert.equal(disabled.managed_mode.phase, 'inactive');
  assert.ok(fixture.calls.filter(call => call.args.includes('stop')).every(call => call.args.at(-1) === serviceName));
});

test('managed feedback lost mode acknowledgment recovers the same committed generation', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  fixture.input.loseModeAcknowledgment = true;
  const enabled = await fixture.invoke('enable', {mode: 'feedback'});
  assert.equal(enabled.managed_mode.generation, 2);
  assert.equal(enabled.managed_mode.verified_for_current_runtime, true);
  assert.equal(fixture.actionCalls('run').length, 2);
});

test('failed feedback admission clear stops only the optional worker and reports incomplete disable', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  await fixture.invoke('enable', {mode: 'feedback'});
  fixture.input.failModeRecord = true;
  await assert.rejects(fixture.invoke('disable', {mode: 'feedback'}), /Owned collector stopped.*not confirmed inactive/);
  assert.equal(fixture.inventory.find(container => container.Config.Labels['com.docker.compose.service'] === serviceName).State.Running, false);
  assert.ok(fixture.inventory.filter(container => container.Config.Labels['com.docker.compose.service'] !== serviceName).every(container => container.State.Running));
  const status = await fixture.invoke('status', {mode: 'feedback'});
  assert.equal(status.running, false); assert.equal(status.managed_mode.verified_for_current_runtime, false);
  delete fixture.input.failModeRecord;
  const recovered = await fixture.invoke('disable', {mode: 'feedback'});
  assert.equal(recovered.changed, true); assert.equal(recovered.managed_mode.phase, 'inactive');
  const stopped = fixture.actionCalls('stop').length;
  const repeated = await fixture.invoke('disable', {mode: 'feedback'});
  assert.equal(repeated.changed, false); assert.equal(repeated.managed_mode.phase, 'inactive');
  assert.equal(fixture.actionCalls('stop').length, stopped);
});

test('unverified API or foreground boot and edited mode override cannot activate feedback', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  for (const [runtime, blocker] of [['api', 'api_feedback_runtime_unverified'], ['foreground-worker', 'foreground_feedback_runtime_unverified']]) {
    fixture.input.failRuntime = runtime;
    const plan = await fixture.invoke('plan', {mode: 'feedback'});
    assert.equal(plan.activation_ready, false); assert.ok(plan.blockers.includes(blocker));
    await assert.rejects(fixture.invoke('enable', {mode: 'feedback'}), new RegExp(blocker));
    assert.equal(fixture.actionCalls('run').length, 0); assert.equal(fixture.actionCalls('up').length, 0);
  }
  delete fixture.input.failRuntime;
  await fixture.invoke('enable', {mode: 'feedback'});
  const release = JSON.parse(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'));
  const changed = JSON.parse(readFileSync(release.gamesMode.override, 'utf8'));
  changed.services[serviceName].command.push('--once');
  writeFileSync(release.gamesMode.override, JSON.stringify(changed));
  await assert.rejects(fixture.invoke('status', {mode: 'feedback'}), /finite supported command/);
});

test('ordinary managed lifecycle clears optional intent without starting the collector', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  await fixture.invoke('enable', {mode: 'feedback'});
  const {createRuntime} = await import('../../scripts/tempo-runtime.mjs');
  const previous = JSON.parse(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'));
  const intent = fixture.input.report.managed_mode.intent;
  const calls = [], records = [];
  const runtime = createRuntime(fixture.target, {revision, previous, fallback: true, stateDirectory: fixture.stateDirectory, log: () => {},
    run: async (_program, args) => {
      calls.push(args);
      if (args.includes('config')) {
        const effective = structuredClone(fixture.configuration);
        for (let index = 0; index < args.length; index++) if (args[index] === '-f') {
          const overlay = JSON.parse(readFileSync(args[index + 1], 'utf8'));
          for (const [name, patch] of Object.entries(overlay.services)) effective.services[name] = {...effective.services[name], ...patch,
            labels: {...effective.services[name]?.labels, ...patch.labels}};
        }
        return {stdout: JSON.stringify(effective)};
      }
      if (args.includes('app.product_composition.game_feedback_mode')) {
        if (args.includes('status')) return {stdout: JSON.stringify({schema_version: 1, read_only: true, intent})};
        const next = JSON.parse(args[args.indexOf('--intent-json') + 1]); records.push(next);
        return {stdout: JSON.stringify({applied: true, generation: next.generation, phase: next.phase})};
      }
      return {stdout: args.includes('ps') ? JSON.stringify([{State: 'running', Service: serviceName}]) : ''};
    }});
  await runtime.config(); await runtime.stopApplications(); await runtime.startServices();
  assert.equal(records.length, 1); assert.equal(records[0].phase, 'inactive'); assert.deepEqual(records[0].runtimes, []);
  assert.ok(calls.filter(args => args.includes('up')).every(args => !args.includes(serviceName)));
});

test('failed optional mode clear cannot block ordinary Study stop and start', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  const {createRuntime} = await import('../../scripts/tempo-runtime.mjs');
  const calls = [];
  const runtime = createRuntime(fixture.target, {revision, previous: {schema: 56}, stateDirectory: fixture.stateDirectory, log: () => {},
    run: async (_program, args) => {
      calls.push(args);
      if (args.includes('config')) return {stdout: JSON.stringify(fixture.configuration)};
      if (args.includes('app.product_composition.game_feedback_mode')) throw new Error('Synthetic optional mode unavailable');
      return {stdout: args.includes('ps') ? JSON.stringify([{State: 'running', Service: 'foreground-worker'}]) : ''};
    }});
  await runtime.config(); await runtime.stopApplications(); await runtime.startServices();
  assert.ok(calls.some(args => args.includes('stop') && args.includes('foreground-worker')));
  assert.ok(calls.some(args => args.includes('up') && args.includes('foreground-worker')));
  assert.ok(calls.filter(args => args.includes('up')).every(args => !args.includes(serviceName)));
  assert.equal(JSON.parse(readFileSync(join(fixture.stateDirectory, 'operation.json'), 'utf8')).games_mode.phase, 'inactive_unconfirmed');
});

test('invalid optional mode overlay is excluded from ordinary Study configuration', async t => {
  const fixture = collectionMainFixture(t, {modern: true});
  await fixture.invoke('enable', {mode: 'feedback'});
  const previous = JSON.parse(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'));
  writeFileSync(previous.gamesMode.override, JSON.stringify({services: {api: {command: ['unexpected']}}}));
  const {createRuntime} = await import('../../scripts/tempo-runtime.mjs');
  const calls = [];
  const runtime = createRuntime(fixture.target, {revision, previous, fallback: true, stateDirectory: fixture.stateDirectory, log: () => {},
    run: async (_program, args) => {calls.push(args); return {stdout: JSON.stringify(fixture.configuration)};}});
  await runtime.config(); await runtime.startServices();
  assert.ok(calls.every(args => !args.includes(previous.gamesMode.override)));
  assert.ok(calls.filter(args => args.includes('up')).every(args => !args.includes(serviceName)));
});

test('collection main blocks persisted pending migration before compose up', async t => {
  const fixture = collectionMainFixture(t);
  writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), JSON.stringify({...verifiedGuard(fixture.target), state: 'pending'}));
  const before = maintenanceBytes(fixture.stateDirectory);
  await assert.rejects(fixture.invoke('enable'), /migration_pending/);
  assert.equal(fixture.actionCalls('up').length, 0);
  assert.equal(fixture.actionCalls('stop').length, 0);
  assert.deepEqual(fixture.lockObservations, [true]);
  assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
});

test('collection full snapshot read has no fixed subprocess deadline while other commands remain bounded', async t => {
  const fixture = collectionMainFixture(t);
  await fixture.invoke('plan');
  const reads = fixture.calls.filter(call => call.args.includes('app.product_composition.game_collection_status'));
  assert.equal(reads.length, 2);
  const identity = reads.find(call => call.args.includes('--identity-only'));
  const full = reads.find(call => !call.args.includes('--identity-only'));
  assert.equal(identity.settings.timeout ?? 30000, 30000);
  assert.equal(full.settings.timeout, 0);
  assert.equal(fixture.calls.filter(call => call.settings.timeout === 0).length, 1);
});

test('collection main blocks observed schema mismatch before compose up', async t => {
  const fixture = collectionMainFixture(t, {observedSchema: 53});
  await assert.rejects(fixture.invoke('enable'), /collection_schema_mismatch/);
  assert.equal(fixture.actionCalls('up').length, 0);
  assert.equal(fixture.actionCalls('stop').length, 0);
  assert.deepEqual(fixture.lockObservations, [true]);
});

test('collection main permits owned disable with unreadable maintenance evidence', async t => {
  const fixture = collectionMainFixture(t, {running: true});
  writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), '{malformed-private-fixture');
  writeFileSync(join(fixture.stateDirectory, 'operation.json'), '{"phase":"complete"}');
  const before = maintenanceBytes(fixture.stateDirectory);
  const output = await fixture.invoke('disable');
  assert.equal(output.running, false); assert.equal(output.changed, true);
  assert.deepEqual(output.maintenance, {status: 'blocked', code: 'collection_maintenance_unavailable'});
  assert.equal(fixture.actionCalls('stop').length, 1);
  assert.equal(fixture.actionCalls('up').length, 0);
  assert.ok(fixture.lockObservations.length > 0 && fixture.lockObservations.every(Boolean));
  assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
});

test('collection main reports launch blockers without mutating on plan or status', async t => {
  const fixture = collectionMainFixture(t, {observedSchema: 53});
  writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), JSON.stringify({...verifiedGuard(fixture.target), state: 'pending'}));
  const before = maintenanceBytes(fixture.stateDirectory);
  for (const operation of ['plan', 'status']) {
    const output = await fixture.invoke(operation);
    assert.equal(output.operation, operation); assert.equal(output.changed, false);
    assert.equal(output.activation_ready, false); assert.equal(output.schema, 54); assert.equal(output.observed_schema, 53);
    assert.deepEqual(output.maintenance, {status: 'blocked', code: 'migration_pending'});
    assert.deepEqual(output.blockers, ['migration_pending', 'collection_schema_mismatch']);
    assert.equal(JSON.stringify(output).includes('fixture-only.dump'), false);
  }
  assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
  writeFileSync(join(fixture.stateDirectory, 'migration-guard.json'), JSON.stringify(verifiedGuard(fixture.target)));
  fixture.input.report.identity.schema_version = undefined;
  const refreshed = await fixture.invoke('status');
  assert.deepEqual(refreshed.maintenance, {status: 'clear'});
  assert.equal(refreshed.observed_schema, null);
  assert.deepEqual(refreshed.blockers, ['full_reader_identity_invalid', 'collection_schema_unverified']);
  assert.deepEqual(maintenanceBytes(fixture.stateDirectory)['operation.json'], before['operation.json']);
  assert.deepEqual(JSON.parse(readFileSync(join(fixture.stateDirectory, 'migration-guard.json'), 'utf8')), verifiedGuard(fixture.target));
  assert.equal(fixture.actionCalls('up').length, 0); assert.equal(fixture.actionCalls('stop').length, 0);
  assert.deepEqual(fixture.lockObservations, [false, false, false]);
});

test('collection main enables once with clear maintenance and matching schema', async t => {
  const fixture = collectionMainFixture(t);
  const before = maintenanceBytes(fixture.stateDirectory);
  const first = await fixture.invoke('enable');
  assert.equal(first.running, true); assert.equal(first.changed, true); assert.equal(first.activation_ready, true);
  assert.deepEqual(first.maintenance, {status: 'clear'}); assert.equal(first.observed_schema, 54);
  const second = await fixture.invoke('enable');
  assert.equal(second.running, true); assert.equal(second.changed, false);
  assert.equal(fixture.actionCalls('up').length, 1);
  const stopped = await fixture.invoke('disable');
  assert.equal(stopped.running, false); assert.equal(stopped.changed, true);
  assert.equal(fixture.actionCalls('stop').length, 1);
  assert.ok(fixture.lockObservations.length > 0 && fixture.lockObservations.every(Boolean));
  assert.deepEqual(maintenanceBytes(fixture.stateDirectory), before);
});

test('collection main rejects missing apply before locking or inspecting', async t => {
  const fixture = collectionMainFixture(t);
  for (const operation of ['enable', 'disable']) await assert.rejects(fixture.invoke(operation, {apply: false}), /--apply/);
  assert.deepEqual(fixture.calls, []);
  assert.deepEqual(fixture.lockObservations, []);
});


const oversizedToken = 'redacted:222222222222';
const bound = () => ({code: 'repertoire_snapshot_exceeds_bound', stage: 'read', sample_complete: false,
  violations: [{dimension: 'source_rows', observed: 10001, limit: 10000, observation: 'lower_bound'}]});
function partialScope(input, both = false) {
  const p = input.report.providers.items[0];
  p.repertoire_readiness.push({repertoire_id: oversizedToken, availability: 'unavailable',
    diagnostic: 'repertoire_snapshot_exceeds_bound', bound: bound()});
  p.selected_repertoire_count = 2;
  if (both) {
    input.report.providers.items[1] = {...structuredClone(p), provider: 'chesscom'};
    input.preview.providers.push({...structuredClone(input.preview.providers[0]), provider: 'chesscom'});
  }
  input.allowances = [oversizedToken];
  return input;
}
const coverage = input => assessSelectedReadiness(input.report, input.allowances, {fullReaderSuccessful: true, collectionOnly: true});

test('collection readiness accepts all selected repertoires beyond eight and checks the last one', () => {
  for (const count of [10, 17]) {
    const input = facts();
    const provider = input.report.providers.items[0];
    provider.repertoire_readiness = Array.from({length: count}, (_, index) => ({
      repertoire_id: 'redacted:' + (index + 1).toString(16).padStart(12, '0'),
      availability: 'available', learner_color: 'white',
    }));
    provider.selected_repertoire_count = count;
    assert.deepEqual(collectionBlockers(input), []);
    assert.equal(coverage(input).comparison_coverage, 'complete');
    provider.repertoire_readiness.at(-1).availability = 'unavailable';
    provider.repertoire_readiness.at(-1).diagnostic = 'repertoire_read_unavailable';
    assert.ok(collectionBlockers(input).includes('lichess:repertoire_readiness_unresolved'));
    assert.equal(coverage(input).comparison_coverage, 'unresolved');
  }
});

test('collection bound allowance is explicit selected scope and shared across providers', () => {
  const input = partialScope(facts(), true);
  assert.deepEqual(collectionBlockers(input), []);
  assert.deepEqual(coverage(input).deferred_comparisons, [{token: oversizedToken,
    providers: ['lichess', 'chesscom'], diagnostic: 'repertoire_snapshot_exceeds_bound', bound: bound(),
    allowance_status: 'explicitly_allowed'}]);
  assert.equal(coverage(input).comparison_coverage, 'partial');
  delete input.allowances;
  assert.ok(collectionBlockers(input).includes('lichess:repertoire_readiness_unresolved'));
});

test('collection allowance cannot suppress malformed bounds incomplete scope or other failures', async t => {
  const cases = {
    'unknown token': i => i.allowances = ['redacted:ffffffffffff'],
    'wrong diagnostic': i => i.report.providers.items[0].repertoire_readiness[1].diagnostic = 'repertoire_read_unavailable',
    'unready publication': i => i.report.providers.items[0].repertoire_readiness[1].publication_state = 'pending',
    'missing publication': i => i.report.providers.items[0].repertoire_readiness[1].publication_state = null,
    'invalid publication revision': i => i.report.providers.items[0].repertoire_readiness[1].publication_revision = 0,
    'full reader writable': i => i.report.identity = {...i.report.identity, transaction_read_only: 'off'},
    'missing bound': i => delete i.report.providers.items[0].repertoire_readiness[1].bound,
    'unknown bound fields': i => i.report.providers.items[0].repertoire_readiness[1].bound.private = 'secret',
    'unknown violation fields': i => i.report.providers.items[0].repertoire_readiness[1].bound.violations[0].private = 'secret',
    'empty violations': i => i.report.providers.items[0].repertoire_readiness[1].bound.violations = [],
    'string measurement': i => i.report.providers.items[0].repertoire_readiness[1].bound.violations[0].observed = '10001',
    'unproved bound': i => i.report.providers.items[0].repertoire_readiness[1].bound.violations[0].observed = 10000,
    'wrong dimension': i => i.report.providers.items[0].repertoire_readiness[1].bound.violations[0].dimension = 'private',
    'truncated': i => i.report.providers.items[0].repertoires_truncated = true,
    'missing count': i => delete i.report.providers.items[0].selected_repertoire_count,
    'mismatched count': i => i.report.providers.items[0].selected_repertoire_count = 3,
    'duplicate token': i => i.report.providers.items[0].repertoire_readiness[0].repertoire_id = oversizedToken,
    'missing provider': i => i.report.providers.items.pop(),
    'selection diagnostic': i => i.report.providers.items[0].selection_diagnostic = 'wrong_scope',
    'zero healthy': i => {i.report.providers.items[0].repertoire_readiness.shift(); i.report.providers.items[0].selected_repertoire_count = 1;},
    'delivery command': i => i.service.command = i.service.command.filter(c => c !== '--collect-only'),
    'pending maintenance': i => i.maintenance = {status: 'blocked', code: 'migration_pending'},
    'identity unavailable': i => i.identityProbe.evidence.identity.availability = 'unavailable',
    'reader writable': i => i.identityProbe.evidence.identity.transaction_read_only = 'off',
    'full reader failed': i => i.fullReaderProbe.status = 'timeout',
  };
  for (const [name, mutate] of Object.entries(cases)) await t.test(name, () => {
    const input = partialScope(facts()); mutate(input);
    assert.notDeepEqual(collectionBlockers(input), []);
  });
  const conflict = partialScope(facts(), true);
  conflict.report.providers.items[1].repertoire_readiness[1].bound.violations[0].observed++;
  assert.ok(collectionBlockers(conflict).includes('selected_repertoire_observations_conflict:' + oversizedToken));
});

test('collection healthy allowed repertoire restores complete selected readiness', () => {
  const input = facts(); input.allowances = ['redacted:111111111111'];
  assert.deepEqual(collectionBlockers(input), []);
  assert.equal(coverage(input).comparison_coverage, 'complete');
  assert.deepEqual(coverage(input).deferred_comparisons, []);
  assert.deepEqual(coverage(input).unused_bound_allowances, input.allowances);
});

function readerFailure(failure, stdout, exitStatus = null) {
  return Object.assign(new Error('private stderr and connection must never escape'), {failure, stdout, exitStatus});
}

test('collection main independently preserves schema when full reader times out', async t => {
  const fixture = collectionMainFixture(t);
  fixture.input.fullRead = () => {throw readerFailure('timeout', '{');};
  const output = await fixture.invoke('status');
  assert.equal(output.observed_schema, 54);
  assert.equal(output.activation_ready, false);
  assert.equal(output.evidence, null);
  assert.deepEqual(output.reader_probes.full, {status: 'timeout', exit_status: null, json_status: 'malformed'});
  assert.ok(output.blockers.includes('full_reader_timeout'));
  assert.equal(output.blockers.includes('collection_schema_unverified'), false);
  assert.equal(JSON.stringify(output).includes('private stderr'), false);
  await assert.rejects(fixture.invoke('enable'), /full_reader_timeout/);
  assert.equal(fixture.actionCalls('up').length, 0);
});

test('collection main requires independent identity and blocks conflicting schema', async t => {
  const fixture = collectionMainFixture(t);
  fixture.input.identityRead = () => {throw readerFailure('nonzero_exit', '{}', 2);};
  let output = await fixture.invoke('status');
  assert.equal(output.observed_schema, null);
  assert.ok(output.blockers.includes('collection_schema_unverified'));
  assert.equal(output.reader_probes.full.status, 'success');
  fixture.input.identityRead = () => JSON.stringify({...fixture.input.identityProbe.evidence,
    identity: {...fixture.input.report.identity, schema_version: 53}});
  output = await fixture.invoke('status');
  assert.ok(output.blockers.includes('collection_schema_observations_conflict'));
  assert.ok(output.blockers.includes('collection_schema_mismatch'));
  await assert.rejects(fixture.invoke('enable'), /collection_schema_observations_conflict/);
  assert.equal(fixture.actionCalls('up').length, 0);
});

test('collection main retains nonzero reader diagnostics without accepting failed or incomplete output', async t => {
  const fixture = collectionMainFixture(t);
  fixture.input.fullRead = () => {throw readerFailure('nonzero_exit', JSON.stringify(fixture.input.report), 2);};
  let output = await fixture.invoke('status');
  assert.deepEqual(output.evidence, fixture.input.report);
  assert.deepEqual(output.reader_probes.full, {status: 'nonzero_exit', exit_status: 2, json_status: 'valid'});
  assert.equal(output.activation_ready, false);
  for (const stdout of ['{', JSON.stringify({...fixture.input.report, truncated: true}), '{}', 'null', 'x'.repeat(4 * 1024 * 1024 + 1)]) {
    fixture.input.fullRead = () => stdout;
    output = await fixture.invoke('status');
    assert.equal(output.activation_ready, false);
    assert.equal(output.evidence, null);
  }
  assert.equal(fixture.actionCalls('up').length, 0);
});

test('collection main allowance is invocation scoped deduplicated and permits only partial collection', async t => {
  const fixture = collectionMainFixture(t);
  partialScope(fixture.input, true);
  const before = readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8');
  await assert.rejects(fixture.invoke('enable'), /repertoire_readiness_unresolved/);
  const output = await fixture.invoke('enable', {allowances: [oversizedToken, oversizedToken]});
  assert.equal(output.activation_ready, true);
  assert.equal(output.comparison_coverage, 'partial');
  assert.equal(output.comparison_coverage_scope, 'selected_repertoire_readiness');
  assert.equal(output.deferred_comparisons.length, 1);
  assert.deepEqual(output.deferred_comparisons[0].providers, ['lichess', 'chesscom']);
  assert.equal(fixture.actionCalls('up').length, 1);
  assert.equal(readFileSync(join(fixture.stateDirectory, 'deployment.json'), 'utf8'), before);
  assert.equal((await fixture.invoke('status')).activation_ready, false);
  assert.equal((await fixture.invoke('disable')).running, false);
});

test('collection main rejects raw wildcard and malformed allowance tokens before external commands', async t => {
  const fixture = collectionMainFixture(t);
  for (const token of ['', 'all', '*', 'private-repertoire', 'redacted:ABCDEF123456', 'redacted:123'])
    await assert.rejects(fixture.invoke('enable', {allowances: [token]}), /exact public redacted repertoire token/);
  assert.equal(fixture.calls.length, 0);
});

test('collection probe failures expose only fixed classifications and complete JSON', () => {
  const probe = readProbe(() => {throw new Error('postgresql://secret');});
  assert.deepEqual(probe, {status: 'process_error', exit_status: null, json_status: 'unavailable', evidence: null});
});


test('collection main healthy allowance is unused and invalid installed API never runs probes', async t => {
  const fixture = collectionMainFixture(t);
  let output = await fixture.invoke('status', {allowances: ['redacted:111111111111']});
  assert.equal(output.activation_ready, true);
  assert.equal(output.comparison_coverage, 'complete');
  assert.deepEqual(output.unused_bound_allowances, ['redacted:111111111111']);
  fixture.input.apiRevision = 'f'.repeat(40);
  const before = fixture.calls.length;
  output = await fixture.invoke('status');
  assert.ok(output.blockers.includes('installed_api_revision_mismatch'));
  assert.equal(output.reader_probes.identity.status, 'unavailable');
  assert.equal(output.reader_probes.full.status, 'unavailable');
  assert.equal(fixture.calls.slice(before).some(call => call.args.includes('exec')), false);
});

test('collection main blocks malformed full-reader identity while retaining independent schema', async t => {
  const fixture = collectionMainFixture(t);
  for (const schema_version of [undefined, null, '54', 0, -1, 54.5, Number.MAX_SAFE_INTEGER + 1]) {
    fixture.input.fullRead = () => JSON.stringify({...fixture.input.report,
      identity: {...fixture.input.report.identity, schema_version}});
    const output = await fixture.invoke('status');
    assert.equal(output.observed_schema, 54);
    assert.equal(output.reader_probes.identity.status, 'success');
    assert.equal(output.reader_probes.full.status, 'success');
    assert.ok(output.blockers.includes('full_reader_identity_invalid'));
    assert.equal(output.activation_ready, false);
    await assert.rejects(fixture.invoke('enable'), /full_reader_identity_invalid/);
  }
  assert.equal(fixture.actionCalls('up').length, 0);
});
