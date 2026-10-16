import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export class StudyPreemptedError extends Error {
  constructor() { super('Optional Maia inference yielded to study'); this.code = 'STUDY_PREEMPTED'; }
}

// A killed process cannot publish a late result. A new process reloads the same
// model and rating adapter; preemption never lowers inference quality.
export function createCancellableMaia({ launch = () => fork(fileURLToPath(new URL('./maia-inference-child.mjs', import.meta.url)), [],
  { stdio: ['ignore', 'inherit', 'inherit', 'ipc'], execArgv: [] }) } = {}) {
  let child, sequence = 0, pending;
  function dispose() { if (child) { const old = child; child = undefined; old.kill('SIGKILL'); } }
  async function analyze(fen, elo, allowed) {
    if (pending) throw new Error('Maia already has an active inference');
    if (!await allowed()) throw new StudyPreemptedError();
    return new Promise((resolve, reject) => {
      const owned = child ??= launch();
      const id = ++sequence;
      let probing = false, stopped = false, settled = false;
      const finish = (error, moves) => {
        if (settled) return;
        settled = true; clearInterval(poll); pending = undefined;
        owned.off('message', message); owned.off('exit', exit); owned.off('error', failure);
        if (error) reject(error); else resolve(moves);
      };
      const stop = () => { if (!stopped && !settled) { stopped = true; dispose(); } };
      const exit = () => { if (child === owned) child = undefined;
        finish(stopped ? new StudyPreemptedError() : new Error('Maia inference process exited')); };
      const failure = error => { dispose(); finish(error); };
      const message = async reply => {
        if (reply.id !== id || stopped || settled || reply.started) return;
        // Recheck after inference so a completed-but-obsolete result is not sent.
        let admitted = false;
        try { admitted = await allowed(); } catch { /* Unavailable control denies publication. */ }
        if (settled || stopped) return;
        if (!admitted) { stop(); return; }
        finish(reply.error ? new Error(reply.error) : undefined, reply.moves);
      };
      const poll = setInterval(async () => {
        if (probing || stopped || settled) return;
        probing = true;
        try { if (!await allowed()) stop(); } catch { stop(); }
        finally { probing = false; }
      }, 75);
      pending = { stop };
      owned.on('message', message); owned.once('exit', exit); owned.once('error', failure);
      owned.send({ id, fen, elo }, error => { if (error) failure(error); });
    });
  }
  return { analyze, close() { pending?.stop(); dispose(); } };
}
