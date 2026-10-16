// @vitest-environment node
import { fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { expect, it, vi } from 'vitest';
import { createCancellableMaia } from '../../scripts/cancellable-maia.mjs';

it('study_priority_kills_confirmed_busy_maia_and_rejects_obsolete_publication', async () => {
  let active = false, processStarted!: () => void;
  const started = new Promise<void>(resolve => { processStarted = resolve; });
  const children: ReturnType<typeof fork>[] = [];
  const inference = createCancellableMaia({ launch: () => {
    const child = fork(fileURLToPath(new URL('../fixtures/study-priority/busy-maia.mjs', import.meta.url)), [], {execArgv:[],stdio:['ignore','ignore','ignore','ipc']});
    child.on('message', (message: {started?:boolean}) => { if(message.started) processStarted(); });
    children.push(child); return child;
  }});
  try {
    const outcome = inference.analyze('fixture',1500,async()=>!active).catch(error=>error);
    await started;
    const before = performance.now(); active = true;
    expect(await outcome).toMatchObject({code:'STUDY_PREEMPTED'});
    expect(performance.now()-before).toBeLessThan(250);
    expect(children[0].signalCode).toBe('SIGKILL');
  } finally { inference.close(); }
});

it('study_priority_control_unavailability_does_not_wait_for_normal_command_retry_backoff',async()=>{
  const {requestMaiaApi}=await import('../../scripts/maia-coverage-client.mjs');
  const fetchImpl=vi.fn(async()=>new Response('Unavailable',{status:503}));const pause=vi.fn();
  await expect(requestMaiaApi('http://fixture','/api/system/foreground-active',{signal:AbortSignal.timeout(100),requestAttempts:1},{fetchImpl,pause})).rejects.toThrow('HTTP 503');
  expect(fetchImpl).toHaveBeenCalledTimes(1);expect(pause).not.toHaveBeenCalled();
});
