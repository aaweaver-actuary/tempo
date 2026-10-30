// Finite managed Games command/receipt vocabulary shared by the two operators.
export const serviceName = 'game-feedback-worker';
export const modes = new Set(['collect-only', 'feedback']);

export function collectorMode(command) {
  if (!Array.isArray(command) || command[0] !== 'python' || command[1] !== '-m' ||
      command[2] !== 'app.product_composition.game_sync_current') return null;
  const seen = new Set();
  let mode = null;
  for (let index = 3; index < command.length; index++) {
    const flag = command[index];
    if (seen.has(flag)) return null;
    seen.add(flag);
    if (flag === '--collect-only' || flag === '--feedback') {
      if (mode !== null) return null;
      mode = flag === '--collect-only' ? 'collect-only' : 'feedback';
    } else if (flag === '--workspace') {
      if (command[++index] !== 'legacy') return null;
    } else if (flag === '--follow') {
      continue;
    } else if (flag === '--overlap-seconds' || flag === '--reconcile-interval-seconds') {
      if (!/^\d+$/.test(command[++index] ?? '')) return null;
    } else return null;
  }
  return mode;
}

export function serviceInMode(service, mode) {
  if (!modes.has(mode) || collectorMode(service?.command) === null) throw new Error('Verified finite collector command required');
  const result = structuredClone(service);
  result.command = result.command.map(value => ['--collect-only', '--feedback'].includes(value)
    ? mode === 'collect-only' ? '--collect-only' : '--feedback' : value);
  result.labels = {...result.labels, 'tempo.collection.mode': mode};
  return result;
}

export function modeOverrideFor(service, mode) {
  return {services: {[serviceName]: {command: serviceInMode(service, mode).command,
    labels: {'tempo.collection.mode': mode}}}};
}

export const modeStatusArgs = workspace => ['exec', '-T', 'api', 'python', '-B', '-m',
  'app.product_composition.game_feedback_mode', 'status', '--workspace', workspace];
export const modeRecordArgs = (intent, generation) => ['run', '--rm', '--no-deps', '-T', serviceName,
  'python', '-B', '-m', 'app.product_composition.game_feedback_mode', 'record', '--workspace', intent.workspace_id,
  '--apply', '--intent-json', JSON.stringify(intent), '--expected-generation', String(generation)];
