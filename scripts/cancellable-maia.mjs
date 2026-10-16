import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export class StudyPreemptedError extends Error {
  constructor() { super('Optional Maia inference yielded to study'); this.code = 'STUDY_PREEMPTED'; }
}

export class MaiaTerminationUnverifiedError extends Error {
  constructor() { super('Maia process termination is unverified; claims remain suspended'); this.code = 'MAIA_TERMINATION_UNVERIFIED'; }
}

function processIsAlive(child) {
  if (child.exitCode != null || child.signalCode != null) return false;
  if (!Number.isInteger(child.pid) || child.pid <= 0) return true;
  try { process.kill(child.pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}

// Publication, process termination and durable release have separate owners.
export function createCancellableMaia({ launch = () => fork(fileURLToPath(new URL('./maia-inference-child.mjs', import.meta.url)), [],
  { stdio: ['ignore', 'inherit', 'inherit', 'ipc'], execArgv: [] }), isProcessAlive = processIsAlive } = {}) {
  let child, sequence = 0, pending, quarantine;
  const terminationVerified = () => {
    if (!quarantine) return true;
    try { if (!isProcessAlive(quarantine)) { quarantine = undefined; return true; } } catch { /* Unknown remains quarantined. */ }
    return false;
  };
  function terminate(owned) {
    if (child === owned) child = undefined;
    quarantine = owned;
    try { owned.kill('SIGKILL'); } catch { /* Watchdog independently verifies termination. */ }
  }
  function launchOwned() {
    const owned = launch();
    owned.once('exit', () => {
      if (child === owned) child = undefined;
      if (quarantine === owned) quarantine = undefined;
    });
    owned.on('error', error => {
      if (child === owned && pending?.owned !== owned) {
        console.error('Idle Maia subprocess failed:', error);
        terminate(owned);
      }
    });
    return owned;
  }
  async function controlAllowed(allowed) {
    let timeout;
    try {
      return await Promise.race([Promise.resolve().then(allowed), new Promise(resolve => { timeout = setTimeout(() => resolve(false), 100); })]);
    } catch { return false; }
    finally { clearTimeout(timeout); }
  }
  async function analyze(fen, elo, allowed) {
    if (pending) throw new Error('Maia already has an active inference');
    if (!terminationVerified()) throw new MaiaTerminationUnverifiedError();
    const request = {stopped: false, stop() { this.stopped = true; }};
    pending = request;
    try {
      if (!await controlAllowed(allowed) || request.stopped) throw new StudyPreemptedError();
      if (!terminationVerified()) throw new MaiaTerminationUnverifiedError();
      return await new Promise((resolve, reject) => {
        const owned = child ??= launchOwned();
        request.owned = owned;
        const id = ++sequence;
        let probing = false, settled = false, watchdog, failure;
        const finish = (error, moves) => {
          if (settled) return;
          settled = true; clearInterval(poll); clearTimeout(watchdog);
          owned.off('message', message); owned.off('exit', exit); owned.off('error', failed);
          if (error) reject(error); else resolve(moves);
        };
        const stoppedResult = () => failure ?? new StudyPreemptedError();
        const stop = () => {
          if (request.stopped || settled) return;
          request.stopped = true;
          watchdog = setTimeout(() => finish(terminationVerified() ? stoppedResult() : new MaiaTerminationUnverifiedError()), 75);
          terminate(owned);
        };
        const exit = () => {
          if (child === owned) child = undefined;
          if (quarantine === owned) quarantine = undefined;
          finish(request.stopped ? stoppedResult() : new Error('Maia inference process exited'));
        };
        const failed = error => { failure = error; stop(); };
        const message = async reply => {
          if (reply.id !== id || request.stopped || settled || reply.started) return;
          const admitted = await controlAllowed(allowed);
          if (settled || request.stopped) return;
          if (!admitted) { stop(); return; }
          finish(reply.error ? new Error(reply.error) : undefined, reply.moves);
        };
        const poll = setInterval(async () => {
          if (probing || request.stopped || settled) return;
          probing = true;
          try { if (!await controlAllowed(allowed)) stop(); }
          finally { probing = false; }
        }, 75);
        request.stop = stop;
        owned.on('message', message); owned.once('exit', exit); owned.once('error', failed);
        try { owned.send({id, fen, elo}, error => { if (error) failed(error); }); }
        catch (error) { failed(error); }
      });
    } finally { if (pending === request) pending = undefined; }
  }
  return {analyze, terminationVerified, close() {
    if (pending) pending.stop();
    if (child) terminate(child);
  }};
}
