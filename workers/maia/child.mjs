import { createModel } from './model.mjs';

let model;
try {
  model = await createModel();
  process.send?.({ type: 'ready', identity: model.identity });
} catch (error) {
  process.send?.({ type: 'startup_error', code: error.code ?? 'model_loading_failed', message: error.message });
  process.exitCode = 1;
  process.disconnect?.();
}

let executing = false;
if (model) {
  process.on('message', async (message) => {
    if (!message || message.type !== 'infer' || executing) return;
    executing = true;
    try {
      const predictions = await model.infer(message.job, { onInferenceStart: () => process.send?.({ type: 'started', token: message.token }) });
      process.send?.({ type: 'result', token: message.token, predictions });
    } catch (error) {
      process.send?.({ type: 'failure', token: message.token, code: error.code ?? 'inference_failed', retryable: error.retryable === true, message: error.message });
    } finally { executing = false; }
  });
  process.on('disconnect', () => { process.exit(0); });
}
