#!/usr/bin/env node
// Exact legacy queue-origin abandonment; default is a checked plan.
import {readFileSync} from 'node:fs';
import {homedir} from 'node:os';
import {join, resolve} from 'node:path';
import {acquireTargetLock, commandExecutor, targetKey} from '../tempo-deployment.mjs';
import {createRuntime} from '../tempo-runtime.mjs';

const args = process.argv.slice(2), values = {};
let apply = false;
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--apply') { if (apply) throw new Error('Duplicate --apply'); apply = true; continue; }
  if (!['--entry', '--card', '--revision', '--operation-id', '--actor', '--reason', '--expected-origin-sha256', '--config'].includes(args[i])
      || !args[i + 1] || values[args[i].slice(2)] !== undefined) throw new Error(`Invalid argument: ${args[i]}`);
  values[args[i].slice(2)] = args[++i];
}
if (!/^[1-9][0-9]*$/.test(values.entry ?? '') || !Number.isSafeInteger(Number(values.entry))
    || !/^[1-9][0-9]*$/.test(values.revision ?? '') || !Number.isSafeInteger(Number(values.revision))
    || !values.card || !values['operation-id'] || !values.actor || !values.reason
    || (apply ? !/^[0-9a-f]{64}$/.test(values['expected-origin-sha256'] ?? '') : values['expected-origin-sha256'] !== undefined))
  throw new Error('Provide exact --entry, --card, --revision, --operation-id, --actor and --reason; --apply also requires --expected-origin-sha256 from the plan.');
const configPath = resolve(values.config ?? process.env.TEMPO_CLI_CONFIG ?? join(homedir(), '.config', 'tempo', 'config.json'));
const registration = JSON.parse(readFileSync(configPath, 'utf8'));
if (registration.version !== 1) throw new Error('A registered version 1 Tempo target is required.');
const target = targetKey(registration);
const stateDirectory = join(process.env.TEMPO_CLI_STATE_DIR ?? join(homedir(), '.local', 'share', 'tempo'), target);
const deployment = JSON.parse(readFileSync(join(stateDirectory, 'deployment.json'), 'utf8'));
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
  const request = {queue_entry_id: Number(values.entry), card_id: values.card, revision: Number(values.revision),
    operation_id: values['operation-id'], actor: values.actor, reason: values.reason};
  if (apply) request.expected_origin_sha256 = values['expected-origin-sha256'];
  const wire = Buffer.from(JSON.stringify({version: 1, plan: !apply, request})).toString('base64');
  const result = await runtime.compose(['exec', '-T', 'foreground-worker', 'python', '-m',
    'tempo_storage.lifecycle.queue_attempt_abandonment', '--request-base64', wire], {timeout: 120000});
  process.stdout.write(result.stdout);
} finally { release(); }
