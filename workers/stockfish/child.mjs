import { createEngine } from './engine.mjs';
let engine, executing = false;
process.on('disconnect', () => process.exit(0));
try { engine = await createEngine(); process.send?.({ type: 'ready', identity: engine.identity }); }
catch (error) { process.send?.({ type: 'startup_error', code: error.code ?? 'engine_loading_failed' }, () => process.exit(1)); }
if (engine) process.on('message', async message => {
  if (message?.type !== 'search' || executing) return;
  executing = true;
  try {
    const result = await engine.search(message.job, () => process.send?.({ type: 'started', token: message.token }));
    process.send?.({ type: 'result', token: message.token, ...result });
  } catch (error) {
    // A malformed UCI stream cannot be reused as a healthy search session.
    process.send?.({ type: 'failure', token: message.token, code: error.code ?? 'engine_search_failed', retryable: false }, () => process.exit(1));
  } finally { executing = false; }
});
