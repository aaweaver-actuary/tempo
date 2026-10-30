#!/usr/bin/env node
// Optional Games lifecycle over the existing registered, frozen managed release.
import { readFileSync, realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { spawnSync } from 'node:child_process';
import { acquireTargetLock, targetKey, validateTarget, validateContainers } from './tempo-deployment.mjs';
import {
  assessMigrationRecovery,
  configurationFingerprint,
} from './tempo-runtime.mjs';

export const serviceName = 'game-feedback-worker';
const owner = 'managed-game-collection';
const textCommand = container => (container.Config?.Cmd ?? []).join(' ');
const labels = container => container.Config?.Labels ?? {};
const active = container => container.State?.Running || container.State?.Restarting;
const collector = command => /(?:game_sync_current|game_worker)(?:\s|$)/.test(command);
const own = (container, target) => labels(container)['com.docker.compose.project'] === target.project &&
  labels(container)['com.docker.compose.service'] === serviceName &&
  labels(container)['tempo.collection.owner'] === owner &&
  labels(container)['tempo.collection.mode'] === 'collect-only' &&
  labels(container)['com.docker.compose.project.working_dir'] === target.root;

const boundCode = 'repertoire_snapshot_exceeds_bound';
const repertoireToken = /^redacted:[0-9a-f]{12}$/;
const boundDimensions = new Set(['source_rows', 'card_rows', 'membership_rows', 'graph_rows',
  'row_bytes', 'estimated_snapshot_bytes', 'snapshot_bytes', 'authored_moves']);
function validBound(bound) {
  return bound && Object.keys(bound).every(key => ['code', 'stage', 'sample_complete', 'violations'].includes(key)) &&
    bound.code === boundCode && ['read', 'projection'].includes(bound.stage) &&
    typeof bound.sample_complete === 'boolean' && Array.isArray(bound.violations) && bound.violations.length > 0 &&
    bound.violations.every(v => v && Object.keys(v).every(key => ['dimension', 'observed', 'limit', 'observation'].includes(key)) &&
      boundDimensions.has(v.dimension) &&
      ['exact', 'lower_bound', 'conservative_estimate'].includes(v.observation) &&
      Number.isSafeInteger(v.observed) && Number.isSafeInteger(v.limit) && v.limit >= 0 && v.observed > v.limit);
}

export function assessSelectedReadiness(report, allowances = [], {fullReaderSuccessful, collectionOnly} = {}) {
  const blockers = [], selected = new Map(), deferred = new Map(), unused = [];
  const providers = report?.providers?.items;
  if (Array.isArray(providers) && !providers.some(p => p?.configured)) blockers.push('collection_configuration_absent');
  if (!Array.isArray(providers) || providers.length !== 2 ||
      new Set(providers.map(p => p?.provider)).size !== 2 ||
      providers.some(p => !['lichess', 'chesscom'].includes(p?.provider) || typeof p.configured !== 'boolean')) {
    blockers.push('selected_repertoire_scope_incomplete');
  }
  for (const p of Array.isArray(providers) ? providers : []) {
    if (!p?.configured) continue;
    const rows = p.repertoire_readiness;
    if (!Array.isArray(rows) || !rows.length || p.repertoires_truncated !== false ||
        !Number.isSafeInteger(p.selected_repertoire_count) || p.selected_repertoire_count !== rows.length ||
        p.selection_diagnostic != null || new Set(rows.map(r => r?.repertoire_id)).size !== rows.length) {
      blockers.push(p.provider + ':selected_repertoire_scope_incomplete');
    }
    let healthy = 0;
    for (const row of Array.isArray(rows) ? rows : []) {
      const token = row?.repertoire_id;
      if (!repertoireToken.test(token ?? '')) {
        blockers.push(p.provider + ':selected_repertoire_token_invalid');
        continue;
      }
      if (selected.has(token) && !isDeepStrictEqual(selected.get(token), row))
        blockers.push('selected_repertoire_observations_conflict:' + token);
      selected.set(token, row);
      const publicationValid = (!Object.hasOwn(row, 'publication_state') || row.publication_state === 'ready') &&
        (!Object.hasOwn(row, 'publication_revision') || Number.isSafeInteger(row.publication_revision) && row.publication_revision > 0);
      if (publicationValid && row.availability === 'available' && ['white', 'black'].includes(row.learner_color) &&
          row.diagnostic == null && row.bound == null) {
        healthy++;
      } else if (publicationValid && allowances.includes(token) && fullReaderSuccessful && collectionOnly &&
          row.availability === 'unavailable' && row.diagnostic === boundCode && validBound(row.bound)) {
        const entry = deferred.get(token) ?? {token, providers: [], diagnostic: boundCode,
          bound: row.bound, allowance_status: 'explicitly_allowed'};
        entry.providers.push(p.provider);
        deferred.set(token, entry);
      } else blockers.push(p.provider + ':repertoire_readiness_unresolved');
    }
    if (!healthy) blockers.push(p.provider + ':healthy_selected_repertoire_required');
  }
  for (const token of allowances) {
    if (!selected.has(token)) blockers.push('allow_bound_repertoire_not_selected:' + token);
    else if (!deferred.has(token) && selected.get(token).availability === 'available') unused.push(token);
  }
  return {blockers, comparison_coverage: blockers.length || !fullReaderSuccessful ? 'unresolved' :
    deferred.size ? 'partial' : 'complete', comparison_coverage_scope: 'selected_repertoire_readiness',
    deferred_comparisons: [...deferred.values()], unused_bound_allowances: unused};
}

// Only the two reader probes preserve command failures as inspectable evidence.
export function readProbe(read) {
  let stdout, status = 'success', exit_status = 0;
  try { stdout = read(); }
  catch (error) {
    status = ['timeout', 'process_error', 'nonzero_exit'].includes(error?.failure) ? error.failure : 'process_error';
    exit_status = Number.isInteger(error?.exitStatus) ? error.exitStatus : null;
    stdout = error?.stdout;
  }
  let evidence = null, json_status = 'unavailable';
  if (typeof stdout === 'string' && Buffer.byteLength(stdout) > 4 * 1024 * 1024) json_status = 'output_bound';
  else if (typeof stdout === 'string') {
    try {
      const value = JSON.parse(stdout);
      if (!value || Array.isArray(value) || value.schema_version !== 1 || value.read_only !== true)
        json_status = 'invalid_envelope';
      else if (value.truncated === true) json_status = 'truncated';
      else { evidence = value; json_status = 'valid'; }
    } catch { json_status = 'malformed'; }
  }
  return {status, exit_status, json_status, evidence};
}
const probeSuccessful = probe => probe?.status === 'success' && probe.json_status === 'valid';
const probeOutcome = probe => ({status: probe.status, exit_status: probe.exit_status, json_status: probe.json_status});

export function collectionBlockers({ candidate, release, service, image, report, preview,
  containers, hostCommands, target, fingerprint, maintenance, identityProbe, fullReaderProbe, allowances = [] }) {
  const blockers = [];
  if (!/^[a-f0-9]{40}$/.test(candidate ?? '') || release.revision !== candidate) blockers.push('installed_candidate_mismatch');
  if (release.configFingerprint !== fingerprint) blockers.push('frozen_configuration_mismatch');
  if (!service || !release.images?.[serviceName]) blockers.push('collector_not_in_frozen_release');
  if (image?.Id !== release.images?.[serviceName] || image?.Config?.Labels?.['org.opencontainers.image.revision'] !== candidate)
    blockers.push('collector_image_unverified');
  const command = service?.command ?? [];
  if (!Array.isArray(command) || !command.includes('--collect-only') || !command.includes('--follow') ||
      !command.includes('app.product_composition.game_sync_current') ||
      service?.environment?.TEMPO_PRODUCT_GAME_SYNC_ENABLED !== '1') blockers.push('collection_only_command_required');
  for (const flag of ['--overlap-seconds', '--reconcile-interval-seconds']) {
    const value = Number(command[command.indexOf(flag) + 1]);
    if (!command.includes(flag) || !/^\d+$/.test(String(command[command.indexOf(flag) + 1] ?? '')) || !Number.isInteger(value) || (flag === '--overlap-seconds' ? value < 0 || value > 3600 : value < 60))
      blockers.push('explicit_' + flag.slice(2).replaceAll('-', '_') + '_required');
  }
  const environment = service?.environment ?? {};
  if (environment.TEMPO_DATABASE_WRITE_URL !== 'postgresql://tempo_writer@postgres:5432/tempo' ||
      environment.TEMPO_DATABASE_READ_URL !== 'postgresql://tempo_writer@postgres:5432/tempo' ||
      environment.PGPASSFILE !== '/run/secrets/worker_pgpass' ||
      !service?.secrets?.some(secret => secret.source === 'writer_pgpass' && secret.target === 'worker_pgpass'))
    blockers.push('collector_database_role_or_secret_mismatch');
  if (Number(service?.mem_limit) !== 256 * 1024 * 1024 || Number(service?.pids_limit) !== 96 ||
      Number(service?.cpus ?? 0) !== 0 || service?.volumes?.length || service?.ports?.length ||
      service?.labels?.['tempo.collection.owner'] !== owner || service?.labels?.['tempo.collection.mode'] !== 'collect-only')
    blockers.push('collector_resource_or_ownership_mismatch');
  if (hostCommands === null) blockers.push('host_process_inventory_unavailable');
  else if (hostCommands.some(collector)) blockers.push('host_collector_conflict');
  const peers = containers.filter(c => collector(textCommand(c)) || labels(c)['com.docker.compose.service'] === serviceName);
  if (peers.some(c => !own(c, target) || !textCommand(c).includes('--collect-only') ||
      c.Mounts?.some(mount => mount.Type === 'volume' || mount.Type === 'bind' && mount.Destination !== '/run/secrets/worker_pgpass') ||
      (active(c) && (c.Image !== release.images?.[serviceName] || JSON.stringify(c.Config?.Cmd) !== JSON.stringify(service?.command) ||
        Number(c.HostConfig?.Memory) !== 256 * 1024 * 1024 || Number(c.HostConfig?.PidsLimit) !== 96 ||
        [c.HostConfig?.NanoCpus, c.HostConfig?.CpuQuota].some(value => Number(value ?? 0) > 0)))) || peers.length > 1)
    blockers.push('competing_or_unverified_collector');
  if (!probeSuccessful(fullReaderProbe)) blockers.push('full_reader_' + (fullReaderProbe?.status === 'success' ? fullReaderProbe.json_status : fullReaderProbe?.status ?? 'unavailable'));
  if (!report || report.truncated === true || report.read_only !== true || report.schema_version !== 1 ||
      ['identity', 'providers', 'collection', 'delivery'].some(section => report[section]?.availability !== 'available'))
    blockers.push('reader_evidence_unavailable');
  if (maintenance?.status !== 'clear') {
    blockers.push(
      maintenance?.code || 'collection_maintenance_unavailable',
    );
  }

  const expectedSchema = release?.schema;
  const identity = identityProbe?.evidence?.identity;
  const observedSchema = identity?.schema_version;
  if (!probeSuccessful(identityProbe)) blockers.push('identity_reader_unavailable');
  if ([identity, report?.identity].some(observation => observation?.availability === 'available' &&
      (observation.reader_role !== 'tempo_reader' || observation.transaction_read_only !== 'on')))
    blockers.push('reader_permission_unverified');
  if (identity?.availability === 'available' && report?.identity?.availability === 'available' &&
      Number.isSafeInteger(observedSchema) && Number.isSafeInteger(report.identity.schema_version) &&
      observedSchema !== report.identity.schema_version) blockers.push('collection_schema_observations_conflict');

  if (
    !probeSuccessful(identityProbe) || identity?.availability !== 'available' ||
    !Number.isSafeInteger(expectedSchema) ||
    expectedSchema < 1 ||
    !Number.isSafeInteger(observedSchema) ||
    observedSchema < 1
  ) {
    blockers.push('collection_schema_unverified');
  } else if (observedSchema !== expectedSchema) {
    blockers.push('collection_schema_mismatch');
  }
  const readiness = assessSelectedReadiness(report, allowances, {fullReaderSuccessful: probeSuccessful(fullReaderProbe),
    collectionOnly: !blockers.includes('collection_only_command_required') && !blockers.includes('competing_or_unverified_collector')});
  blockers.push(...readiness.blockers);
  const providers = Array.isArray(report?.providers?.items) ? report.providers.items : [];
  const configured = providers.filter(p => p?.configured);
  if (report?.providers?.availability === 'available' && !configured.length) blockers.push('collection_configuration_absent');
  for (const p of providers) {
    if (!p || typeof p !== 'object') continue;
    if (p.configuration_ambiguous || p.current_jobs?.length && !p.configured) blockers.push(p.provider + ':ambiguous_or_unconfigured_pending_work');
    if (!p.configured) continue; // An unused provider need not be enabled.
    if (Object.values(p.required_inputs ?? {}).some(value => value !== true) || !Object.keys(p.required_inputs ?? {}).length)
      blockers.push(p.provider + ':required_inputs_absent');
    if (!p.configuration?.interval_seconds) blockers.push(p.provider + ':explicit_recurrence_required');
    const scope = preview?.providers?.find(row => row.provider === p.provider);
    if (!scope?.configured || !scope.configuration) blockers.push(p.provider + ':forward_preview_unavailable');
    else {
      const width = (Date.parse(scope.configuration.window.end_at) - Date.parse(scope.configuration.window.start_at)) / 1000;
      const overlap = Number(command[command.indexOf('--overlap-seconds') + 1]);
      if (!Number.isFinite(width) || width < 60 || width > 86400 || overlap >= width) blockers.push(p.provider + ':invalid_forward_seed');
    }
  }
  return [...new Set(blockers)];
}

export async function operateCollection(operation, { apply = false, inspect, lock, start, stop }) {
  if (!['plan', 'status', 'enable', 'disable'].includes(operation)) throw new Error('Expected plan, status, enable or disable');
  if (['enable', 'disable'].includes(operation) && !apply) throw new Error(operation + ' requires explicit --apply');
  const releaseLock = ['enable', 'disable'].includes(operation) ? lock() : () => {};
  try {
    const state = await inspect();
    if (operation === 'enable') {
      if (state.blockers.length) throw new Error('Collection blocked: ' + state.blockers.join(', '));
      if (!state.running) await start(state);
    } else if (operation === 'disable') {
      if (!state.stopSafe) throw new Error('Collector identity/ownership unverified; no service stopped');
      if (state.running) await stop(state);
    }
    const changed = operation === 'enable' ? !state.running : operation === 'disable' && state.running;
    const finalState = changed ? await inspect() : state;
    return { ...finalState.output, operation, changed };
  } finally { releaseLock(); }
}

function command(program, args) {
  const result = spawnSync(program, args, { encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024 });
  if (result.error || result.status !== 0) {
    const error = new Error(program + ' inspection/action failed');
    error.failure = result.error?.code === 'ETIMEDOUT' ? 'timeout' : result.error || !Number.isInteger(result.status) ? 'process_error' : 'nonzero_exit';
    error.exitStatus = Number.isInteger(result.status) ? result.status : null;
    error.stdout = result.stdout;
    throw error;
  }
  return result.stdout;
}
const jsonFile = path => JSON.parse(readFileSync(path, 'utf8'));

export function readCollectionMaintenance({
  stateDirectory,
  target,
  configuration,
  readFile = readFileSync,
}) {
  function optionalObject(filename) {
    let text;
    try {
      text = readFile(join(stateDirectory, filename), 'utf8');
    } catch (error) {
      if (error?.code === 'ENOENT') return null;
      throw error;
    }

    const value = JSON.parse(text);
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('Invalid maintenance evidence');
    }
    return value;
  }

  try {
    const guard = optionalObject('migration-guard.json');

    // Match the existing runtime: the operation is relevant when no guard exists.
    const operation = guard === null
      ? optionalObject('operation.json')
      : null;

    const assessment = assessMigrationRecovery({
      guard,
      operation,
      target,
      configuration,
      retry: false,
    });

    if (assessment.status === 'clear') {
      return { status: 'clear' };
    }

    if (assessment.status === 'blocked' && typeof assessment.code === 'string') {
      return { status: 'blocked', code: assessment.code };
    }

    return { status: 'blocked', code: 'collection_maintenance_unavailable' };
  } catch {
    return { status: 'blocked', code: 'collection_maintenance_unavailable' };
  }
}

export async function main(
  argv = process.argv.slice(2),
  { runCommand = command } = {},
) {
  const operation = argv.shift();
  let configPath = join(homedir(), '.config/tempo/config.json'), candidate;
  let apply = false;
  const allowances = [];
  while (argv.length) {
    const option = argv.shift();
    if (option === '--apply') apply = true;
    else if (option === '--candidate') candidate = argv.shift();
    else if (option === '--allow-bound-repertoire') {
      const token = argv.shift();
      if (!repertoireToken.test(token ?? '')) throw new Error('--allow-bound-repertoire requires an exact public redacted repertoire token');
      if (!allowances.includes(token)) allowances.push(token);
    }
    else if (option === '--config') configPath = argv.shift();
    else throw new Error('Unknown option: ' + option);
  }
  if (!candidate || !/^[a-f0-9]{40}$/.test(candidate)) throw new Error('--candidate requires the exact qualified commit');
  const target = jsonFile(configPath);
  if (target.version !== 1) throw new Error('Unsupported managed registration; preserve the installed authority');
  const stateDirectory = join(process.env.TEMPO_CLI_STATE_DIR ?? join(homedir(), '.local/share/tempo'), targetKey(target));
  const receiptPath = join(stateDirectory, 'deployment.json');
  const docker = args => runCommand('docker', ['--context', target.context, ...args]);
  let compose, snapshot;
  const inspect = async () => {
    const registrationText = readFileSync(configPath, 'utf8'), receiptText = readFileSync(receiptPath, 'utf8');
    const release = JSON.parse(receiptText);
    if (docker(['info', '--format', '{{.ID}}']).trim() !== target.daemonId) throw new Error('Registered Docker daemon identity changed');
    if (!release.composeFiles?.length || !release.imageOverride) throw new Error('Frozen managed release receipt required');
    const prefix = ['compose', '--project-directory', target.root, '--project-name', target.project,
      '--env-file', target.envFile, ...release.composeFiles.flatMap(file => ['-f', file]), '-f', release.imageOverride, '--profile', 'maintenance', '--profile', 'games-feedback'];
    compose = args => docker([...prefix, ...args]);
    const configuration = JSON.parse(compose(['config', '--format', 'json']));
    validateTarget(configuration, target);
    const maintenance = readCollectionMaintenance({
      stateDirectory,
      target,
      configuration,
    });
    const service = configuration.services[serviceName];
    const ids = docker(['ps', '-aq']).trim().split(/\s+/).filter(Boolean);
    const containers = ids.length ? JSON.parse(docker(['inspect', ...ids])) : [];
    validateContainers(containers, target);
    const api = containers.filter(c => active(c) && labels(c)['com.docker.compose.project'] === target.project &&
      labels(c)['com.docker.compose.service'] === 'api');
    let identityProbe = {status: 'unavailable', exit_status: null, json_status: 'unavailable', evidence: null};
    let fullReaderProbe = {...identityProbe};
    let report = null, preview = null, image = null, hostCommands = null;
    if (release.images?.[serviceName]) image = JSON.parse(docker(['image', 'inspect', release.images[serviceName]]))[0];
    let apiRevisionVerified = false;
    if (api.length === 1 && api[0].Image === release.images?.api) {
      const apiImage = JSON.parse(docker(['image', 'inspect', api[0].Image]))[0];
      apiRevisionVerified = apiImage.Config?.Labels?.['org.opencontainers.image.revision'] === candidate;
    }
    if (apiRevisionVerified) {
      const readerCommand = ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_collection_status'];
      identityProbe = readProbe(() => compose([...readerCommand, '--identity-only']));
      fullReaderProbe = readProbe(() => compose([...readerCommand, '--workspace', 'legacy', '--limit', '20']));
      report = fullReaderProbe.evidence;
      if (service?.command?.includes('--overlap-seconds')) {
        try { preview = JSON.parse(compose(['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_sync_current', '--preview', '--follow', '--overlap-seconds', service.command[service.command.indexOf('--overlap-seconds') + 1]])); } catch { /* Block enable. */ }
      }
    }
    try { hostCommands = runCommand('ps', ['-axo', 'command=']).split('\n').filter(line => !line.includes('game-review-collection.mjs')); } catch { /* Fail closed for enable. */ }
    const owned = containers.filter(c => labels(c)['com.docker.compose.project'] === target.project && labels(c)['com.docker.compose.service'] === serviceName);
    const running = owned.some(active);
    const stopSafe = owned.every(c => own(c, target) && (!active(c) || c.Image === release.images?.[serviceName]) && textCommand(c).includes('--collect-only'));
    const blockers = collectionBlockers({ candidate, release, service, image, report, preview, containers, hostCommands, target,
      fingerprint: configurationFingerprint(configuration), maintenance, identityProbe, fullReaderProbe, allowances });
    if (api.length !== 1 || api[0].Image !== release.images?.api) blockers.push('installed_api_identity_unverified');
    else if (!apiRevisionVerified) blockers.push('installed_api_revision_mismatch');
    const readiness = assessSelectedReadiness(report, allowances, {fullReaderSuccessful: probeSuccessful(fullReaderProbe),
      collectionOnly: apiRevisionVerified && !blockers.includes('collection_only_command_required') &&
        !blockers.includes('competing_or_unverified_collector')});
    snapshot = { registrationText, receiptText };
    return { blockers, running, stopSafe, output: { candidate, installed_revision: release.revision, schema: release.schema,
      maintenance,
      ...Object.fromEntries(Object.entries(readiness).filter(([key]) => key !== 'blockers')),
      reader_probes: {identity: probeOutcome(identityProbe), full: probeOutcome(fullReaderProbe)},
      identity_evidence: identityProbe.evidence?.identity ?? null,
      observed_schema:
        identityProbe.evidence?.identity?.availability === 'available' &&
        Number.isSafeInteger(identityProbe.evidence.identity.schema_version) &&
        identityProbe.evidence.identity.schema_version > 0
          ? identityProbe.evidence.identity.schema_version
          : null,
      collector_image: release.images?.[serviceName] ?? null, mode: 'collect-only', running, activation_ready: !blockers.length,
      blockers, evidence: report, preview, resources: service ? { cpus: service.cpus ?? null, memory: service.mem_limit, pids: service.pids_limit } : null,
      retained_legacy_producers: containers.filter(c => active(c) && /background-worker|foreground-worker|background-scheduler/.test(labels(c)['com.docker.compose.service'] ?? '')).map(c => ({ id: c.Id, image: c.Image, service: labels(c)['com.docker.compose.service'] })) } };
  };
  const unchanged = () => {
    if (readFileSync(configPath, 'utf8') !== snapshot.registrationText || readFileSync(receiptPath, 'utf8') !== snapshot.receiptText)
      throw new Error('Managed registration/release changed during inspection');
  };
  const output = await operateCollection(operation, { apply, inspect, lock: () => acquireTargetLock(stateDirectory),
    start: async () => { unchanged(); compose(['up', '-d', '--no-build', '--no-deps', '--pull', 'never', '--wait', '--wait-timeout', '30', serviceName]);
      const after = await inspect(); if (!after.running || after.blockers.length) throw new Error('Collector post-start verification failed; inspect status before retry'); },
    stop: async () => { unchanged(); compose(['stop', '--timeout', '30', serviceName]);
      if ((await inspect()).running) throw new Error('Collector still running after stop'); } });
  console.log(JSON.stringify(output));
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 2; });
}
