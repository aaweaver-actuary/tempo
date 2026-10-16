// Lifecycle fixture only. Production child selection is fixed in worker.mjs.
const identity = { model_id: 'test-maia', model_version: 'fixture-v1', runtime_name: 'fixture', runtime_version: '1', implementation_version: 'fixture-v1' };
if (process.argv.includes('--startup-fail')) {
  process.send({ type: 'startup_error', code: 'model_asset_missing', message: 'Fixture missing model' });
} else {
  const startupDelay = Number(process.argv.find((argument) => argument.startsWith('--startup-delay='))?.split('=')[1] ?? 0);
  setTimeout(() => process.send({ type: 'ready', identity }), startupDelay);
  process.on('message', (message) => {
    if (message.type !== 'infer') return;
    const { token, job } = message;
    process.send({ type: 'started', token });
    if (job.fen === 'crash') { process.exit(17); return; }
    if (job.fen === 'hang' || (job.fen === 'fenced' && job.execution_generation === 1)) { while (true) { Math.sqrt(Math.random()); } }
    if (job.fen === 'failure') { process.send({ type: 'failure', token, code: 'invalid_fen', retryable: false }); return; }
    const predictions = job.legal_moves.map((move) => ({ move, probability: 1 / job.legal_moves.length }));
    if (job.fen === 'wrong_moves') predictions[0].move = 'a1a2';
    if (job.fen === 'invalid_mass') predictions[0].probability = 0.25;
    if (job.fen === 'invalid_number') predictions[0].probability = NaN;
    setTimeout(() => process.send({ type: 'result', token, predictions }), job.fen === 'delay' ? 60 : 5);
  });
}
process.on('disconnect', () => process.exit(0));
