#!/usr/bin/env node
// Optional Games lifecycle over the existing registered, frozen managed release.
import { readFileSync, realpathSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import { spawnSync } from 'node:child_process';
import { acquireTargetLock, atomicJson, targetKey, validateTarget, validateContainers } from './tempo-deployment.mjs';
import {
  assessMigrationRecovery,
  configurationFingerprint,
} from './tempo-runtime.mjs';
import {collectorMode, modeOverrideFor, modeRecordArgs, modes, modeStatusArgs, serviceInMode, serviceName} from './managed-games-control.mjs';
export {collectorMode, serviceInMode, serviceName} from './managed-games-control.mjs';

const owner = 'managed-game-collection';
const feedbackCapabilities = ['selector', 'current_authority', 'again', 'canonical_introduction',
  'retained_qualification', 'foreground_handoff', 'issuance_fence'];
const textCommand = container => (container.Config?.Cmd ?? []).join(' ');
const labels = container => container.Config?.Labels ?? {};
const active = container => container.State?.Running || container.State?.Restarting;
const collector = command => /(?:game_sync_current|game_worker)(?:\s|$)/.test(command);
const own = (container, target) => labels(container)['com.docker.compose.project'] === target.project &&
  labels(container)['com.docker.compose.service'] === serviceName &&
  labels(container)['tempo.collection.owner'] === owner &&
  modes.has(labels(container)['tempo.collection.mode']) &&
  collectorMode(container.Config?.Cmd) === labels(container)['tempo.collection.mode'] &&
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
  containers, hostCommands, target, fingerprint, maintenance, identityProbe, fullReaderProbe, allowances = [],
  mode = 'collect-only', currentService = service, allowModeTransition = false }) {
  const blockers = [];
  if (!/^[a-f0-9]{40}$/.test(candidate ?? '') || release.revision !== candidate) blockers.push('installed_candidate_mismatch');
  if (release.configFingerprint !== fingerprint) blockers.push('frozen_configuration_mismatch');
  if (!service || !release.images?.[serviceName]) blockers.push('collector_not_in_frozen_release');
  if (image?.Id !== release.images?.[serviceName] || image?.Config?.Labels?.['org.opencontainers.image.revision'] !== candidate)
    blockers.push('collector_image_unverified');
  const command = Array.isArray(service?.command) ? service.command : [];
  const commandRequired = mode === 'collect-only' ? 'collection_only_command_required' : 'feedback_command_required';
  if (collectorMode(command) !== mode || !command.includes('--follow') ||
      !command.includes('app.product_composition.game_sync_current') ||
      service?.environment?.TEMPO_PRODUCT_GAME_SYNC_ENABLED !== '1') blockers.push(commandRequired);
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
  if (Number(service?.mem_limit) !== 1024 * 1024 * 1024 || Number(service?.pids_limit) !== 96 ||
      Number(service?.cpus ?? 0) !== 0 || service?.volumes?.length || service?.ports?.length ||
      service?.labels?.['tempo.collection.owner'] !== owner || service?.labels?.['tempo.collection.mode'] !== mode)
    blockers.push('collector_resource_or_ownership_mismatch');
  if (hostCommands === null) blockers.push('host_process_inventory_unavailable');
  else if (hostCommands.some(collector)) blockers.push('host_collector_conflict');
  const peers = containers.filter(c => collector(textCommand(c)) || labels(c)['com.docker.compose.service'] === serviceName);
  if (peers.some(c => !own(c, target) ||
      c.Mounts?.some(mount => mount.Type === 'volume' || mount.Type === 'bind' && mount.Destination !== '/run/secrets/worker_pgpass') ||
      (active(c) && (c.Image !== release.images?.[serviceName] || JSON.stringify(c.Config?.Cmd) !== JSON.stringify(
        allowModeTransition && collectorMode(c.Config?.Cmd) === collectorMode(currentService?.command) ? currentService?.command : service?.command) ||
        Number(c.HostConfig?.Memory) !== 1024 * 1024 * 1024 || Number(c.HostConfig?.PidsLimit) !== 96 ||
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
  if (report?.identity?.availability === 'available' &&
      (!Number.isSafeInteger(report.identity.schema_version) || report.identity.schema_version < 1))
    blockers.push('full_reader_identity_invalid');
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
    collectionOnly: mode === 'collect-only' && !blockers.includes(commandRequired) && !blockers.includes('competing_or_unverified_collector')});
  blockers.push(...readiness.blockers);
  if (mode === 'feedback') {
    if (allowances.length) blockers.push('bound_allowance_collection_only');
    const capability = report?.feedback_capabilities;
    if (capability?.availability !== 'available' || capability.policy_version !== 'first_departure_v1' ||
        capability.receipt_interface_version !== '0.2.2' || feedbackCapabilities.some(name => capability[name] !== true))
      blockers.push('feedback_capabilities_unverified');
    if (report?.managed_mode?.availability !== 'available' || Number(expectedSchema) < 56)
      blockers.push('managed_feedback_mode_bridge_unavailable');
    const ci = release.evidence;
    // The managed installer records an explicit --no-ci choice in its frozen
    // receipt. Preserve that fact without manufacturing successful CI evidence.
    const ciDisabled = ci?.verification === 'ci-disabled' &&
      !Object.hasOwn(ci, 'run_id') && !Object.hasOwn(ci, 'url');
    const verifiedRun = ci?.verification == null && Number.isSafeInteger(ci?.run_id) && ci.run_id > 0 &&
      ci.url === 'https://github.com/aaweaver-actuary/tempo/actions/runs/' + ci.run_id;
    if (ci?.commit !== candidate || !(ciDisabled || verifiedRun))
      blockers.push('feedback_qualified_release_evidence_required');
  }
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

export async function operateCollection(operation, { apply = false, inspect, lock, start, stop,
  mode = 'collect-only', prepareMode, deactivate, activate }) {
  if (!['plan', 'status', 'enable', 'disable'].includes(operation)) throw new Error('Expected plan, status, enable or disable');
  if (['enable', 'disable'].includes(operation) && !apply) throw new Error(operation + ' requires explicit --apply');
  const releaseLock = ['enable', 'disable'].includes(operation) ? lock() : () => {};
  try {
    let state = await inspect();
    let changed = false;
    if (operation === 'enable') {
      if (state.blockers.length) throw new Error('Collection blocked: ' + state.blockers.join(', '));
      if (state.running && (state.observedMode ?? mode) !== mode) {
        if (!state.stopSafe) throw new Error('Collector identity/ownership unverified; no mode transition');
        await deactivate?.(state, 'transitioning');
        await stop(state);
        changed = true;
        state = await inspect();
        if (state.running) throw new Error('Previous collector still running; no replacement started');
      }
      if (!state.running) {
        await deactivate?.(state, 'transitioning');
        await prepareMode?.(state);
        await start(state);
        changed = true;
        state = await inspect();
      }
      if (!state.modeIntentVerified) changed = Boolean(await activate?.(state)) || changed;
    } else if (operation === 'disable') {
      if (!state.stopSafe) throw new Error('Collector identity/ownership unverified; no service stopped');
      let admissionError;
      try { changed = Boolean(await deactivate?.(state, 'inactive')); } catch (error) { admissionError = error; }
      if (state.running) await stop(state);
      changed = state.running || changed;
      if (admissionError) throw new Error('Owned collector stopped, but feedback admission was not confirmed inactive; inspect status and repeat disable --apply after restoring the managed writer');
    }
    const finalState = changed ? await inspect() : state;
    return { ...finalState.output, operation, changed };
  } finally { releaseLock(); }
}

function command(program, args, {timeout = 30000} = {}) {
  const result = spawnSync(program, args, { encoding: 'utf8', timeout, maxBuffer: 4 * 1024 * 1024 });
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
  let configPath = join(homedir(), '.config/tempo/config.json'), candidate, mode = 'collect-only';
  let apply = false;
  const allowances = [];
  while (argv.length) {
    const option = argv.shift();
    if (option === '--apply') apply = true;
    else if (option === '--candidate') candidate = argv.shift();
    else if (option === '--mode') {
      mode = argv.shift();
      if (!modes.has(mode)) throw new Error('--mode must be collect-only or feedback');
    }
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
  const docker = (args, settings) => runCommand('docker', ['--context', target.context, ...args], settings);
  let compose, snapshot, last;
  const operationId = randomUUID();
  const inspect = async () => {
    const registrationText = readFileSync(configPath, 'utf8'), receiptText = readFileSync(receiptPath, 'utf8');
    const release = JSON.parse(receiptText);
    if (docker(['info', '--format', '{{.ID}}']).trim() !== target.daemonId) throw new Error('Registered Docker daemon identity changed');
    if (!release.composeFiles?.length || !release.imageOverride) throw new Error('Frozen managed release receipt required');
    const prefix = ['compose', '--project-directory', target.root, '--project-name', target.project,
      '--env-file', target.envFile, ...release.composeFiles.flatMap(file => ['-f', file]), '-f', release.imageOverride, '--profile', 'maintenance', '--profile', 'games-feedback'];
    const baseCompose = (args, settings) => docker([...prefix, ...args], settings);
    const baseConfiguration = JSON.parse(baseCompose(['config', '--format', 'json']));
    let modeOverride = null;
    if (release.gamesMode !== undefined) {
      const saved = release.gamesMode;
      const expectedPath = join(dirname(release.imageOverride), 'games-mode.json');
      if (saved?.version !== 1 || !modes.has(saved.mode) || saved.override !== expectedPath ||
          saved.baseFingerprint !== configurationFingerprint(baseConfiguration)) throw new Error('Managed mode release evidence is invalid');
      const expected = modeOverrideFor(baseConfiguration.services?.[serviceName], saved.mode);
      if (!isDeepStrictEqual(jsonFile(saved.override), expected)) throw new Error('Managed mode override differs from its finite supported command');
      modeOverride = saved.override;
    }
    compose = (args, settings) => docker([...prefix, ...(modeOverride ? ['-f', modeOverride] : []), ...args], settings);
    const configuration = JSON.parse(compose(['config', '--format', 'json']));
    validateTarget(configuration, target);
    const maintenance = readCollectionMaintenance({
      stateDirectory,
      target,
      configuration,
    });
    const currentService = configuration.services[serviceName];
    const configuredMode = collectorMode(currentService?.command);
    const service = configuredMode ? serviceInMode(currentService, mode) : currentService;
    const ids = docker(['ps', '-aq']).trim().split(/\s+/).filter(Boolean);
    const containers = ids.length ? JSON.parse(docker(['inspect', ...ids])) : [];
    validateContainers(containers, target);
    const api = containers.filter(c => active(c) && labels(c)['com.docker.compose.project'] === target.project &&
      labels(c)['com.docker.compose.service'] === 'api');
    let identityProbe = {status: 'unavailable', exit_status: null, json_status: 'unavailable', evidence: null};
    let fullReaderProbe = {...identityProbe};
    let report = null, preview = null, image = null, hostCommands = null;
    let modeEvidence = {availability: 'unavailable', intent: null};
    const verifiedRuntimes = [];
    if (release.images?.[serviceName]) image = JSON.parse(docker(['image', 'inspect', release.images[serviceName]]))[0];
    let apiRevisionVerified = false;
    if (api.length === 1 && api[0].Image === release.images?.api) {
      const apiImage = JSON.parse(docker(['image', 'inspect', api[0].Image]))[0];
      apiRevisionVerified = apiImage.Config?.Labels?.['org.opencontainers.image.revision'] === candidate;
    }
    if (apiRevisionVerified) {
      const readerCommand = ['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_collection_status'];
      identityProbe = readProbe(() => compose([...readerCommand, '--identity-only']));
      fullReaderProbe = readProbe(() => compose([...readerCommand, '--workspace', 'legacy', '--limit', '20'], {timeout: 0}));
      report = fullReaderProbe.evidence;
      if (release.schema >= 56) {
        try {
          const result = JSON.parse(compose(modeStatusArgs('legacy')));
          if (result.schema_version === 1 && result.read_only === true && Object.hasOwn(result, 'intent'))
            modeEvidence = {availability: 'available', intent: result.intent};
        } catch { /* Exact missing mode evidence blocks feedback, never ordinary Study. */ }
      }
      if (service?.command?.includes('--overlap-seconds')) {
        try { preview = JSON.parse(compose(['exec', '-T', 'api', 'python', '-B', '-m', 'app.product_composition.game_sync_current', '--preview', '--follow', '--overlap-seconds', service.command[service.command.indexOf('--overlap-seconds') + 1]])); } catch { /* Block enable. */ }
      }
    }
    try { hostCommands = runCommand('ps', ['-axo', 'command=']).split('\n').filter(line => !line.includes('game-review-collection.mjs')); } catch { /* Fail closed for enable. */ }
    const owned = containers.filter(c => labels(c)['com.docker.compose.project'] === target.project && labels(c)['com.docker.compose.service'] === serviceName);
    const running = owned.some(active);
    const observedMode = running ? collectorMode(owned.find(active)?.Config?.Cmd) : null;
    const stopSafe = owned.every(c => own(c, target) && (!active(c) || c.Image === release.images?.[serviceName] &&
      JSON.stringify(c.Config?.Cmd) === JSON.stringify(currentService?.command)));
    if (modeEvidence.availability === 'available') {
      for (const name of ['api', 'foreground-worker', serviceName]) {
        const matches = containers.filter(c => active(c) && labels(c)['com.docker.compose.project'] === target.project &&
          labels(c)['com.docker.compose.service'] === name && labels(c)['com.docker.compose.project.working_dir'] === target.root &&
          c.Image === release.images?.[name] && c.Config?.Hostname === c.Id?.slice(0, 12));
        if (matches.length !== 1) continue;
        try {
          const candidateRuntime = matches[0];
          const imageFact = JSON.parse(docker(['image', 'inspect', candidateRuntime.Image]))[0];
          if (imageFact.Config?.Labels?.['org.opencontainers.image.revision'] !== candidate) continue;
          const read = JSON.parse(compose(['exec', '-T', name, 'python', '-B', '-m',
            'app.product_composition.managed_runtime', 'inspect']));
          if (read.schema_version !== 1 || read.read_only !== true || read.runtime?.schema_version !== 1 ||
              read.runtime.hostname !== candidateRuntime.Config.Hostname || !/^[0-9a-f]{32}$/.test(read.runtime.boot_nonce ?? '')) continue;
          verifiedRuntimes.push({service: name, container_id: candidateRuntime.Id, image: candidateRuntime.Image,
            hostname: read.runtime.hostname, boot_nonce: read.runtime.boot_nonce});
        } catch { /* Missing/restarted identity leaves new Games handoff unverified. */ }
      }
    }
    const blockers = collectionBlockers({ candidate, release, service, image, report, preview, containers, hostCommands, target,
      fingerprint: configurationFingerprint(configuration), maintenance, identityProbe, fullReaderProbe, allowances,
      mode, currentService, allowModeTransition: true });
    if (api.length !== 1 || api[0].Image !== release.images?.api) blockers.push('installed_api_identity_unverified');
    else if (!apiRevisionVerified) blockers.push('installed_api_revision_mismatch');
    if (mode === 'feedback') {
      if (!verifiedRuntimes.some(runtime => runtime.service === 'api')) blockers.push('api_feedback_runtime_unverified');
      if (!verifiedRuntimes.some(runtime => runtime.service === 'foreground-worker')) blockers.push('foreground_feedback_runtime_unverified');
    }
    const readiness = assessSelectedReadiness(report, allowances, {fullReaderSuccessful: probeSuccessful(fullReaderProbe),
      collectionOnly: mode === 'collect-only' && apiRevisionVerified && !blockers.includes('collection_only_command_required') &&
        !blockers.includes('competing_or_unverified_collector')});
    const targetFingerprint = createHash('sha256').update(targetKey(target)).digest('hex');
    const requiredRuntimes = mode === 'feedback' ? verifiedRuntimes : verifiedRuntimes.filter(runtime => runtime.service === serviceName);
    const recorded = modeEvidence.intent;
    const modeIntentVerified = Boolean(running && observedMode === mode && recorded?.phase === 'active' && recorded.mode === mode &&
      recorded.release_revision === candidate && recorded.collector_image === release.images?.[serviceName] &&
      recorded.configuration_fingerprint === configurationFingerprint(configuration) && recorded.target_fingerprint === targetFingerprint &&
      isDeepStrictEqual(recorded.runtimes, requiredRuntimes));
    snapshot = { registrationText, receiptText };
    last = {release, baseConfiguration, configuration, configuredMode, prefix, modeEvidence, verifiedRuntimes, targetFingerprint};
    return { blockers, running, stopSafe, observedMode, modeIntentVerified, output: { candidate, installed_revision: release.revision, schema: release.schema,
      maintenance, release_verification: release.evidence ?? null,
      ...Object.fromEntries(Object.entries(readiness).filter(([key]) => key !== 'blockers')),
      reader_probes: {identity: probeOutcome(identityProbe), full: probeOutcome(fullReaderProbe)},
      identity_evidence: identityProbe.evidence?.identity ?? null,
      observed_schema:
        identityProbe.evidence?.identity?.availability === 'available' &&
        Number.isSafeInteger(identityProbe.evidence.identity.schema_version) &&
        identityProbe.evidence.identity.schema_version > 0
          ? identityProbe.evidence.identity.schema_version
          : null,
      collector_image: release.images?.[serviceName] ?? null, mode, observed_mode: observedMode,
      intended_mode: recorded?.mode ?? release.gamesMode?.mode ?? 'collect-only',
      managed_mode: {availability: modeEvidence.availability, phase: recorded?.phase ?? 'inactive',
        generation: recorded?.generation ?? null, verified_for_current_runtime: modeIntentVerified},
      runtime_verification: Object.fromEntries(['api', 'foreground-worker', serviceName].map(name =>
        [name, verifiedRuntimes.some(runtime => runtime.service === name)])),
      running, activation_ready: !blockers.length,
      blockers, evidence: report, preview, resources: service ? { cpus: service.cpus ?? null, memory: service.mem_limit, pids: service.pids_limit } : null,
      retained_legacy_producers: containers.filter(c => active(c) && /background-worker|foreground-worker|background-scheduler/.test(labels(c)['com.docker.compose.service'] ?? '')).map(c => ({ id: c.Id, image: c.Image, service: labels(c)['com.docker.compose.service'] })) } };
  };
  const unchanged = () => {
    if (readFileSync(configPath, 'utf8') !== snapshot.registrationText || readFileSync(receiptPath, 'utf8') !== snapshot.receiptText)
      throw new Error('Managed registration/release changed during inspection');
  };
  const recordIntent = async (state, phase) => {
    unchanged();
    const needed = last.release.schema >= 56 || last.configuredMode === 'feedback' || mode === 'feedback';
    if (!needed) return false; // An unchanged old collection-only release has no new handoff path.
    if (last.modeEvidence.availability !== 'available') throw new Error('Managed feedback admission cannot be verified');
    const before = JSON.parse(compose(modeStatusArgs('legacy')));
    if (before.schema_version !== 1 || before.read_only !== true || !Object.hasOwn(before, 'intent'))
      throw new Error('Managed mode reader returned incomplete evidence');
    const previous = before.intent;
    const generation = previous?.generation ?? 0;
    if (!Number.isSafeInteger(generation) || generation < 0) throw new Error('Managed mode generation unavailable');
    const selectedMode = phase === 'inactive' ? previous?.mode ?? last.configuredMode ?? mode : mode;
    const runtimes = phase === 'active' ? mode === 'feedback' ? last.verifiedRuntimes :
      last.verifiedRuntimes.filter(runtime => runtime.service === serviceName) : [];
    if (phase === 'active' && (!state.running || state.observedMode !== mode ||
        runtimes.length !== (mode === 'feedback' ? 3 : 1))) throw new Error('Running mode and actual boot identities are not verified');
    if (phase === 'inactive' && previous?.phase === 'inactive' && previous.release_revision === candidate &&
        previous.configuration_fingerprint === configurationFingerprint(last.configuration)) return false;
    const now = new Date().toISOString();
    const intent = {schema_version: 1, workspace_id: 'legacy', generation: generation + 1,
      mode: selectedMode, phase, operation_id: operationId, release_revision: candidate,
      collector_image: last.release.images[serviceName], configuration_fingerprint: configurationFingerprint(last.configuration),
      target_fingerprint: last.targetFingerprint, runtimes, updated_at: now, verified_at: phase === 'active' ? now : null};
    try {
      const result = JSON.parse(compose(modeRecordArgs(intent, generation)));
      if (result.applied !== true || result.generation !== intent.generation || result.phase !== phase ||
          result.mode !== selectedMode || result.operation_id !== operationId) throw new Error('Managed mode acknowledgment incomplete');
    } catch {
      // A lost acknowledgment may have committed. Inspect this exact operation
      // before any retry; never mint a second generation to hide uncertainty.
      let observed;
      try { observed = JSON.parse(compose(modeStatusArgs('legacy'))).intent; } catch { /* Remains uncertain. */ }
      if (!isDeepStrictEqual(observed, intent)) throw new Error('Managed mode acknowledgment uncertain; inspect status before retry');
    }
    await inspect();
    return true;
  };
  const prepareMode = async () => {
    unchanged();
    if (last.configuredMode === mode) return;
    const override = join(dirname(last.release.imageOverride), 'games-mode.json');
    const document = modeOverrideFor(last.baseConfiguration.services[serviceName], mode);
    atomicJson(override, document);
    const next = JSON.parse(docker([...last.prefix, '-f', override, 'config', '--format', 'json']));
    const expected = structuredClone(last.baseConfiguration);
    expected.services[serviceName] = serviceInMode(expected.services[serviceName], mode);
    if (!isDeepStrictEqual(next, expected)) throw new Error('Mode transition changed more than its supported command and label');
    const release = {...last.release, configFingerprint: configurationFingerprint(next), gamesMode: {version: 1, mode,
      override, baseFingerprint: configurationFingerprint(last.baseConfiguration)}};
    atomicJson(receiptPath, release);
    const after = await inspect();
    if (after.blockers.length) throw new Error('Mode transition is not ready; collector remains stopped: ' + after.blockers.join(', '));
  };
  const output = await operateCollection(operation, { apply, inspect, lock: () => acquireTargetLock(stateDirectory),
    mode, prepareMode, deactivate: recordIntent,
    activate: async state => {
      const changed = await recordIntent(state, 'active');
      if (last.release.schema >= 56 && !(await inspect()).modeIntentVerified)
        throw new Error('Feedback mode verification incomplete; run status, then disable --apply for the exact candidate');
      return changed;
    },
    start: async () => { unchanged(); compose(['up', '-d', '--no-build', '--no-deps', '--pull', 'never', '--wait', '--wait-timeout', '30', serviceName]);
      const after = await inspect(); if (!after.running || after.observedMode !== mode || after.blockers.length) throw new Error('Collector post-start verification failed; inspect status before retry'); },
    stop: async () => { unchanged(); compose(['stop', '--timeout', '30', serviceName]);
      if ((await inspect()).running) throw new Error('Collector still running after stop'); } });
  console.log(JSON.stringify(output));
}

if (process.argv[1] && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 2; });
}
