import childProcess from 'node:child_process';
import { EventEmitter } from 'node:events';
import { syncBuiltinESMExports } from 'node:module';
import { writeFile } from 'node:fs/promises';

const scenario = process.argv[2];
const events = [];
const outbox = process.env.TEMPO_ENGINE_OUTBOX_PATH;
if (scenario === 'recovery') await writeFile(outbox, JSON.stringify({path:'/api/games/analysis/position/old/report', options:{method:'POST',body:'{"lease_id":"original-lease"}'}, operationId:'original-operation'}));
childProcess.fork = () => {
  events.push({type:'fork'});
  const child = new EventEmitter();
  child.send = ({command}, callback) => {
    events.push({type:'uci', command});
    callback?.();
    if (command.startsWith('go ')) queueMicrotask(() => {
      child.emit('message', {text:'info depth 4 multipv 1 score cp 12 pv a2a3'});
      child.emit('message', {text:'bestmove a2a3'});
    });
  };
  child.kill = () => {};
  queueMicrotask(() => child.emit('message', {ready:true}));
  return child;
};
syncBuiltinESMExports();
let claimed = false;
globalThis.fetch = async (url, options = {}) => {
  const path = new URL(url).pathname;
  events.push({type:'request', path, body:options.body, operation:options.headers?.['Idempotency-Key']});
  let result = {};
  if (path === '/api/system/foreground-active') result = scenario === 'study' ? {study_only_mode:true} : scenario === 'busy' ? {active:true,available:true} : {active:false,available:true};
  else if (path === '/api/defensive-threats/analysis/claim') result = {job:null};
  else if (path === '/api/games/analysis/position/claim') {
    const job = scenario === 'finalize' ? {kind:'finalize', lease_id:'original-lease'} : scenario === 'search' ? {kind:'position',id:'job-1',lease_id:'original-lease',request:{position_start_fen:'8/8/8/8/8/8/8/8 w - - 0 1',position_prefix_uci:[],multipv:1,depth:4}} : null;
    result = {job: claimed ? null : job}; claimed = true;
  }
  return new Response(JSON.stringify(result), {status:200, headers:{'Content-Type':'application/json'}});
};
const originalTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => {
  if (ms === 2000 || ms === 5000) {
    process.stdout.write(`TRACE ${JSON.stringify(events)}\n`, () => process.exit(0));
    return {unref(){}};
  }
  return originalTimeout(fn, ms, ...args);
};
await import('../../../scripts/defense-engine-worker.mjs');
