import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
// Keep engine threads in an owned child so an undrained search can be killed
// before network delivery of its durable release.
export async function createStockfishProcess() {
  const child = fork(fileURLToPath(new URL('./stockfish-child.mjs', import.meta.url)), [],
    { stdio: ['ignore', 'inherit', 'inherit', 'ipc'], execArgv: [] });
  const engine = { listen: () => {}, onError: () => {},
    uci(command) { child.send({ command }, error => { if (error) engine.onError(String(error)); }); },
    terminate() { child.kill('SIGKILL'); } };
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { engine.terminate(); reject(new Error('Stockfish startup timed out')); }, 30_000);
    child.once('error', reject);
    child.once('exit', () => { clearTimeout(timeout); reject(new Error('Stockfish startup exited')); });
    child.on('message', message => {
      if (message.ready) { clearTimeout(timeout); resolve(); }
      else if (message.error) engine.onError(message.error);
      else if (message.text) engine.listen(message.text);
    });
  });
  child.on('exit', () => engine.onError('Stockfish process exited'));
  return engine;
}
