#!/usr/bin/env node
// Local coordinator entry point. Default is an inspection-only plan.
import {existsSync, readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join, resolve} from 'node:path';
import {acquireTargetLock, commandExecutor, targetKey} from '../tempo-deployment.mjs';
import {createRuntime} from '../tempo-runtime.mjs';

const args = process.argv.slice(2);
const values = {};
let apply = false;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--apply') { apply = true; continue; }
  if (!['--action', '--repertoire', '--epoch', '--operation-id', '--qualification', '--config', '--new-epoch', '--page-size'].includes(args[i]) || !args[i + 1])
    throw new Error(`Unrecognized or incomplete argument: ${args[i]}`);
  values[args[i].slice(2)] = args[++i];
}
if (!['seed', 'stage', 'activate', 'pause', 'resume', 'rollforward'].includes(values.action)
    || !values.repertoire || !values.epoch || !values['operation-id'])
  throw new Error('Provide --action, --repertoire, --epoch and --operation-id.');
if (values.action === 'rollforward' && (!values['new-epoch'] || values['new-epoch'] === values.epoch))
  throw new Error('Rollforward requires a distinct --new-epoch.');
const pageSize = values['page-size'] === undefined ? 100 : Number(values['page-size']);
if (values.action !== 'seed' && values['page-size'] !== undefined) throw new Error('--page-size is only valid for seed.');
if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100 || (values['page-size'] !== undefined && !/^[1-9][0-9]*$/.test(values['page-size'])))
  throw new Error('--page-size must be an integer from 1 to 100.');
const configPath = resolve(values.config ?? process.env.TEMPO_CLI_CONFIG ?? join(homedir(), '.config', 'tempo', 'config.json'));
const registration = JSON.parse(readFileSync(configPath, 'utf8'));
if (registration.version !== 1) throw new Error('A registered version 1 Tempo target is required.');
const target = targetKey(registration);
const stateDirectory = join(process.env.TEMPO_CLI_STATE_DIR ?? join(homedir(), '.local', 'share', 'tempo'), target);
const deployment = JSON.parse(readFileSync(join(stateDirectory, 'deployment.json'), 'utf8'));
const guardPath = join(stateDirectory, 'migration-guard.json');
const operationPath = join(stateDirectory, 'operation.json');
const guard = existsSync(guardPath) ? JSON.parse(readFileSync(guardPath, 'utf8')) : null;
const operation = existsSync(operationPath) ? JSON.parse(readFileSync(operationPath, 'utf8')) : null;
const qualification = values.qualification ? JSON.parse(readFileSync(resolve(values.qualification), 'utf8')) : null;
const revision = deployment.revision;
if (!/^[0-9a-f]{40}$/.test(revision ?? '')) throw new Error('Installed deployment revision is unavailable.');
const release = acquireTargetLock(stateDirectory);
try {
  const run = commandExecutor({root: registration.root, output: () => {}});
  const runtime = createRuntime(registration, {run, stateDirectory, revision, previous: deployment, fallback: true, log: () => {}});
  await runtime.inspectTarget();
  const image = await runtime.inspectRunningRevision();
  if (image.status !== 'consistent' || !image.detail.startsWith(revision + ' ')
      || image.receipt !== 'matches immutable images and configuration of inspected running services')
    throw new Error('Running service identities do not match the recorded deployment.');
  const payload = {version: 1, action: values.action, repertoire_id: values.repertoire,
    epoch: values.epoch, page_size: pageSize, new_epoch: values['new-epoch'] ?? null, operation_id: values['operation-id'], plan: !apply,
    release: {registration, deployment, guard, qualification, target, revision, operation}};
  const wire = Buffer.from(JSON.stringify(payload)).toString('base64');
  if (wire.length > 131072) throw new Error('Operator evidence exceeds the bounded command size.');
  const result = await runtime.compose(['exec', '-T', 'foreground-worker', 'python', '-m',
    'tempo_legacy_database.adapters.study.position_owner_operator', '--request-base64', wire],
    {timeout: 120000});
  process.stdout.write(result.stdout);
} finally {
  release();
}
