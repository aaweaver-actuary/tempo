import { spawn, fork } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createEvaluator, EvaluationError, LIMITS } from './evaluate.mjs';

/** Reuses the existing bundled Stockfish child/assets, or an explicitly configured native binary. */
export function createOwnedLauncher({executable, spawnProcess=spawn, forkProcess=fork, closeGraceMs=75, killWaitMs=500}={}) {
  if (executable!==undefined && (typeof executable!=='string' || !executable.startsWith('/'))) throw new TypeError('Use an absolute executable path');
  for (const timeout of [closeGraceMs,killWaitMs]) if (!Number.isInteger(timeout)||timeout<1||timeout>1000) throw new TypeError('Invalid cleanup timeout');
  return ({onLine,onError})=>{
    const child=executable
      ? spawnProcess(executable,[],{stdio:['pipe','pipe','ignore'],shell:false})
      : forkProcess(fileURLToPath(new URL('../../scripts/stockfish-child.mjs',import.meta.url)),[],
        {stdio:['ignore','ignore','ignore','ipc'],execArgv:[]});
    let closed=false, closing=false, ready=Boolean(executable), pending=[], buffer='';
    const exited=new Promise(resolve=>child.once('close',()=>{closed=true;resolve();}));
    child.on('error',()=>onError());
    child.on('exit',()=>{if(!closing)onError();});
    function send(command) {
      if (closing||closed) throw new EvaluationError('engine_exit');
      if (executable) child.stdin.write(command+'\n',error=>{if(error&&!closing)onError();});
      else if (!ready) pending.push(command);
      else child.send({command},error=>{if(error&&!closing)onError();});
    }
    if (executable) {child.stdin.on('error',()=>{if(!closing)onError();});child.stdout.on('error',()=>onError());}
    if (executable) child.stdout.setEncoding('utf8').on('data',chunk=>{
      buffer+=chunk;
      let newline;
      while ((newline=buffer.indexOf('\n'))>=0) {
        const line=buffer.slice(0,newline).replace(/\r$/,'');buffer=buffer.slice(newline+1);onLine(line);
      }
      if (Buffer.byteLength(buffer)>LIMITS.lineBytes) {buffer='';onLine('x'.repeat(LIMITS.lineBytes+1));}
    });
    else child.on('message',message=>{
      if (message.ready) {ready=true;const queued=pending;pending=[];queued.forEach(send);}
      else if (message.error) onError();
      else if (message.text!==undefined) onLine(message.text);
    });
    async function waitExit(ms) {
      let timer;
      await Promise.race([exited,new Promise(resolve=>{timer=setTimeout(resolve,ms);})]);
      clearTimeout(timer);
    }
    async function close() {
      if (closed) return;
      try {send('stop');send('quit');} catch { /* still terminate our child below */ }
      closing=true;pending=[];
      await waitExit(closeGraceMs);
      if (!closed) {try {child.kill('SIGKILL');} catch { /* report unverifiable termination */ }}
      await waitExit(killWaitMs);
      if (!closed) throw new EvaluationError('cleanup_unverified');
    }
    return {send,close};
  };
}

export function createStockfishEvaluator(options={}) {
  const {executable,...timeouts}=options;
  return createEvaluator({...timeouts,launch:createOwnedLauncher({executable})});
}

/** One JSON document in, one correlated bounded JSON document out, then exit. No daemon or queue. */
export async function runStdio({input=process.stdin,output=process.stdout,evaluator=createStockfishEvaluator({
  executable:process.env.TEMPO_STOCKFISH_EXECUTABLE}),signal,inputTimeoutMs=2000}={}) {
  let raw='', bytes=0, request_id=null, timer;
  try {
    const reading=(async()=>{
      for await (const chunk of input) {
        bytes+=Buffer.byteLength(chunk);
        if (bytes>LIMITS.requestBytes) throw new EvaluationError('request_limit');
        raw+=chunk.toString();
      }
    })();
    await Promise.race([reading,new Promise((_,reject)=>{timer=setTimeout(()=>{
      input.destroy();reject(new EvaluationError('input_timeout'));
    },inputTimeoutMs);})]);
    clearTimeout(timer);
    let request;
    try {request=JSON.parse(raw);} catch {throw new EvaluationError('invalid_json');}
    if (typeof request.request_id==='string' && /^[A-Za-z0-9._:-]{1,100}$/.test(request.request_id)) request_id=request.request_id;
    const result=await evaluator.evaluate(request,{signal});
    if (result.request_id!==request_id) throw new EvaluationError('response_identity_mismatch');
    const encoded=JSON.stringify(result);
    if (Buffer.byteLength(encoded)>LIMITS.responseBytes) throw new EvaluationError('response_limit');
    output.write(encoded+'\n');
    return result;
  } catch(error) {
    const result={request_id,status:'error',error:{code:error instanceof EvaluationError?error.code:'adapter_error'}};
    output.write(JSON.stringify(result)+'\n');
    return result;
  } finally {clearTimeout(timer);}
}

if (process.argv[1] && fileURLToPath(import.meta.url)===process.argv[1]) {
  const cancellation=new AbortController();
  process.once('SIGTERM',()=>cancellation.abort());
  process.once('SIGINT',()=>cancellation.abort());
  const result=await runStdio({signal:cancellation.signal});
  process.exitCode=result.status==='error'?1:0;
}
