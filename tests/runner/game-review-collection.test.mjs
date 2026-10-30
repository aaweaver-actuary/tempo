import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { collectionBlockers, main, operateCollection, readCollectionMaintenance, serviceName } from '../../scripts/game-review-collection.mjs';
import { targetKey } from '../../scripts/tempo-deployment.mjs';
import { configurationFingerprint } from '../../scripts/tempo-runtime.mjs';
import { cliFixture } from './tempo-cli-fixture.mjs';
const revision = 'a'.repeat(40), imageId = 'sha256:' + 'b'.repeat(64);
function facts() {
  const configuration = { interval_seconds: 60, window: {start_at: '2026-10-29T10:00:00Z', end_at: '2026-10-29T11:00:00Z'} };
  return { candidate: revision, release: { revision, schema: 54, configFingerprint: 'frozen', images: {[serviceName]: imageId} }, fingerprint: 'frozen',
    maintenance: {status: 'clear'},
    target: {project: 'fixture', root: '/fixture'}, hostCommands: [], containers: [],
    service: { command: ['python', '-m', 'app.product_composition.game_sync_current', '--collect-only', '--follow', '--overlap-seconds', '300', '--reconcile-interval-seconds', '600'],
      environment: {TEMPO_PRODUCT_GAME_SYNC_ENABLED: '1', TEMPO_DATABASE_WRITE_URL: 'postgresql://tempo_writer@postgres:5432/tempo',
        TEMPO_DATABASE_READ_URL: 'postgresql://tempo_writer@postgres:5432/tempo', PGPASSFILE: '/run/secrets/worker_pgpass'},
      secrets: [{source: 'writer_pgpass', target: 'worker_pgpass'}], mem_limit: 268435456, pids_limit: 96,
      labels: {'tempo.collection.owner': 'managed-game-collection', 'tempo.collection.mode': 'collect-only'} },
    image: {Id: imageId, Config: {Labels: {'org.opencontainers.image.revision': revision}}},
    report: {schema_version: 1, read_only: true, identity: {availability: 'available', schema_version: 54}, collection: {availability: 'available'}, delivery: {availability: 'available'},
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
        assert.deepEqual(collectionBlockers(input), ['collection_schema_unverified']);
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

function collectionMainFixture(t, {running = false, observedSchema = 54} = {}) {
  const fixture = cliFixture();
  t.after(() => rmSync(fixture.directory, {recursive: true, force: true}));
  const configuration = JSON.parse(readFileSync(join(fixture.directory, 'fixture.json'), 'utf8')).config;
  const input = facts();
  configuration.services[serviceName] = input.service;
  const apiImage = 'sha256:' + 'c'.repeat(64);
  configuration.services.api.image = apiImage;
  configuration.services[serviceName].image = imageId;
  fixture.target.envFile = join(fixture.directory, 'fixture.env');
  writeFileSync(fixture.target.envFile, '');
  writeFileSync(fixture.registration, JSON.stringify(fixture.target));
  writeFileSync(fixture.target.composeFiles[0], JSON.stringify(configuration));
  const imageOverride = join(fixture.directory, 'recorded-images.json');
  writeFileSync(imageOverride, JSON.stringify({services: {api: {image: apiImage}, [serviceName]: {image: imageId}}}));
  writeFileSync(join(fixture.stateDirectory, 'deployment.json'), JSON.stringify({version: 1, revision, schema: 54,
    images: {api: apiImage, [serviceName]: imageId}, composeFiles: fixture.target.composeFiles, imageOverride,
    configFingerprint: configurationFingerprint(configuration)}));
  input.report.identity.schema_version = observedSchema;
  const api = {Id: 'fixture-api', Image: apiImage, State: {Running: true, Restarting: false},
    Config: {Cmd: ['fixture-api'], Labels: {'com.docker.compose.project': fixture.target.project,
      'com.docker.compose.service': 'api', 'com.docker.compose.project.working_dir': fixture.target.root}}, Mounts: []};
  const collector = {Id: 'fixture-collector', Image: imageId, State: {Running: running, Restarting: false},
    HostConfig: {Memory: 268435456, PidsLimit: 96, NanoCpus: 0, CpuQuota: 0},
    Config: {Cmd: [...input.service.command], Labels: {...input.service.labels,
      'com.docker.compose.project': fixture.target.project, 'com.docker.compose.service': serviceName,
      'com.docker.compose.project.working_dir': fixture.target.root}}, Mounts: []};
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
  const runCommand = (program, args) => {
    calls.push({program, args: [...args]});
    if (program === 'ps' && same(args, ['-axo', 'command='])) return '';
    if (program === 'docker' && same(args.slice(0, 2), ['--context', fixture.target.context])) {
      const action = args.slice(2);
      if (same(action, ['info', '--format', '{{.ID}}'])) {
        lockObservations.push(existsSync(join(fixture.stateDirectory, 'maintenance.lock')));
        return fixture.target.daemonId;
      }
      if (same(action, ['ps', '-aq'])) return 'fixture-api\nfixture-collector\n';
      if (same(action, ['inspect', 'fixture-api', 'fixture-collector'])) return JSON.stringify([api, collector]);
      if (same(action, ['image', 'inspect', imageId]) || same(action, ['image', 'inspect', apiImage])) {
        return JSON.stringify([{Id: action[2], Config: {Labels: {'org.opencontainers.image.revision': revision}}}]);
      }
      if (same(action.slice(0, composePrefix.length), composePrefix)) {
        const composeAction = action.slice(composePrefix.length);
        if (same(composeAction, ['config', '--format', 'json'])) return JSON.stringify(configuration);
        if (same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_collection_status', '--workspace', 'legacy', '--limit', '20'])) {
          return JSON.stringify(input.report);
        }
        if (same(composeAction, ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_sync_current', '--preview', '--follow', '--overlap-seconds', '300'])) {
          return JSON.stringify(input.preview);
        }
        if (same(composeAction, ['up', '-d', '--no-build', '--no-deps', '--pull', 'never', '--wait', '--wait-timeout', '30', serviceName])) {
          assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), true);
          collector.State.Running = true;
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
  const invoke = async (operation, {apply = true} = {}) => {
    try {
      await main([operation, '--config', fixture.registration, '--candidate', revision,
        ...(['enable', 'disable'].includes(operation) && apply ? ['--apply'] : [])], {runCommand});
      return outputs.at(-1);
    } finally {
      assert.deepEqual(unexpected, [], 'fixture must recognize every external command');
      assert.equal(existsSync(join(fixture.stateDirectory, 'maintenance.lock')), false);
    }
  };
  return {...fixture, invoke, actionCalls, calls, lockObservations, configuration, input};
}

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
  assert.deepEqual(refreshed.blockers, ['collection_schema_unverified']);
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
