import { Chess, validateFen } from 'chess.js';

/** @typedef {import('../../app/features/product-api/contracts').EngineEvidence['context']} EngineContext */
/** @typedef {import('../../app/features/product-api/contracts').EngineEvidence} EngineEvidence */
/** @typedef {{request_id:string, context:EngineContext, candidates:readonly string[]}} EvaluationRequest */

export const LIMITS = Object.freeze({ candidates:16, history:512, requestBytes:32768,
  responseBytes:32768, engineBytes:1048576, lineBytes:16384, depth:32,
  nodes:1000000, timeMs:1000, threads:1, hashMb:64 });
const MOVE = /^[a-h][1-8][a-h][1-8][qrbn]?$/;
const CONTEXT_KEYS = ['starting_fen','history_uci','target_fen','variant','learner_color',
  'engine_version','max_depth','max_nodes','max_time_ms','threads','hash_mb'];
export class EvaluationError extends Error {
  constructor(code) { super(code); this.name = 'EvaluationError'; this.code = code; }
}
const fail = code => { throw new EvaluationError(code); };
const integer = (value, min, max) => Number.isSafeInteger(value) && value >= min && value <= max;
function keys(value, expected) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value,key));
}
function move(board, uci) {
  if (typeof uci !== 'string' || !MOVE.test(uci)) fail('illegal_move');
  try { return board.move({from:uci.slice(0,2),to:uci.slice(2,4),promotion:uci[4]}); }
  catch { fail('illegal_move'); }
}
export function validateRequest(input) {
  let encoded;
  try { encoded = JSON.stringify(input); } catch { fail('invalid_request'); }
  if (!encoded || Buffer.byteLength(encoded) > LIMITS.requestBytes) fail('request_limit');
  const request = JSON.parse(encoded); // detach caller-owned values before the first await
  if (!keys(request,['request_id','context','candidates']) || typeof request.request_id !== 'string'
      || !/^[A-Za-z0-9._:-]{1,100}$/.test(request.request_id)) fail('invalid_request_identity');
  const context = request.context;
  if (!keys(context,CONTEXT_KEYS)) fail('invalid_context');
  if (context.variant !== 'standard') fail('unsupported_variant');
  if (!['white','black'].includes(context.learner_color)) fail('invalid_context');
  if (typeof context.engine_version !== 'string' || !context.engine_version.trim()
      || context.engine_version.length > 200 || /[\r\n]/.test(context.engine_version)) fail('invalid_context');
  if (!integer(context.threads,1,LIMITS.threads) || !integer(context.hash_mb,1,LIMITS.hashMb)) fail('resource_limit');
  for (const [key,max] of [['max_depth',LIMITS.depth],['max_nodes',LIMITS.nodes],['max_time_ms',LIMITS.timeMs]]) {
    if (context[key] !== null && !integer(context[key],1,max)) fail('search_limit');
  }
  if ([context.max_depth,context.max_nodes,context.max_time_ms].every(value=>value===null)) fail('unbounded_search');
  if (!Array.isArray(context.history_uci) || context.history_uci.length > LIMITS.history) fail('history_limit');
  for (const fen of [context.starting_fen,context.target_fen]) {
    if (typeof fen !== 'string' || fen.length > 120 || fen.trim().split(/\s+/).length !== 6
        || /[\r\n]/.test(fen) || !validateFen(fen).ok) fail('invalid_fen');
  }
  const board = new Chess(context.starting_fen);
  for (const uci of context.history_uci) move(board,uci);
  if (board.fen({forceEnpassantSquare:true}) !== new Chess(context.target_fen).fen({forceEnpassantSquare:true})) fail('context_mismatch');
  if (!Array.isArray(request.candidates) || !request.candidates.length || request.candidates.length > LIMITS.candidates
      || new Set(request.candidates).size !== request.candidates.length) fail('candidate_limit');
  for (const uci of request.candidates) move(new Chess(board.fen({forceEnpassantSquare:true})),uci);
  return {request,board};
}
function unavailable(move_uci, reason, facts = {}) {
  return {move_uci,score:null,score_perspective:'learner',depth:facts.depth??null,nodes:facts.nodes??null,unavailable_reason:reason};
}
function freeze(value) {
  if (value && typeof value==='object') { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function parseInfo(line, board, sign) {
  if (!line.startsWith('info ') || line.startsWith('info string ') || !/ score /.test(line)) return null;
  const score = / score (cp|mate) (-?\d+)(?= |$)/.exec(line);
  const depth = Number(/ depth (\d+)(?= |$)/.exec(line)?.[1]);
  const nodesText = / nodes (\d+)(?= |$)/.exec(line)?.[1];
  const nodes = nodesText === undefined ? null : Number(nodesText);
  const pv = / pv (.+)$/.exec(line)?.[1]?.split(/\s+/);
  const rank = Number(/ multipv (\d+)(?= |$)/.exec(line)?.[1]??1);
  if (!score || !integer(depth,1,128) || !integer(rank,1,1) || !pv?.length || pv.length>256
      || (nodes!==null && !integer(nodes,0,Number.MAX_SAFE_INTEGER))) fail('malformed_uci');
  const value = Number(score[2])*sign;
  if (!Number.isSafeInteger(value) || (score[1]==='mate' && Math.abs(value)>1000)) fail('malformed_uci');
  const replay = new Chess(board.fen({forceEnpassantSquare:true}));
  try { for (const uci of pv) move(replay,uci); } catch { fail('malformed_uci'); }
  let reason = /\blowerbound\b/.test(line)?'score_lowerbound':/\bupperbound\b/.test(line)?'score_upperbound':null;
  if (reason && sign<0) reason=reason==='score_lowerbound'?'score_upperbound':'score_lowerbound';
  return {move_uci:pv[0], score:reason?null:{kind:score[1]==='cp'?'centipawns':'mate',value},
    score_perspective:'learner',depth,nodes,unavailable_reason:reason};
}

/**
 * One owned process per request; concurrent calls fail busy instead of building an unbounded queue.
 * launch({onLine,onError}) must return {send(command), close():Promise<void>} synchronously.
 * close resolves only after the owned process exits, or rejects cleanup_unverified.
 */
export function createEvaluator({launch, startupTimeoutMs=2000, searchTimeoutMs=1500, totalTimeoutMs=15000}={}) {
  if (typeof launch!=='function') throw new TypeError('An owned UCI process factory is required');
  for (const timeout of [startupTimeoutMs,searchTimeoutMs,totalTimeoutMs]) {
    if (!integer(timeout,1,15000)) throw new TypeError('Invalid timeout');
  }
  let active=false, quarantined=false;
  async function evaluate(input,{signal}={}) {
    const {request,board}=validateRequest(input);
    if (quarantined) fail('cleanup_unverified');
    if (active) fail('busy');
    if (signal?.aborted) fail('cancelled');
    active=true;
    let transport, waiting, fault, bytes=0, version=null;
    const started=performance.now(), observations=new Map();
    const stop = code => {
      fault ??= new EvaluationError(code);
      waiting?.reject(fault);
    };
    const onAbort=()=>stop('cancelled');
    const onLine = line => {
      if (fault) return;
      try {
        if (typeof line!=='string') fail('malformed_uci');
        bytes+=Buffer.byteLength(line);
        if (Buffer.byteLength(line)>LIMITS.lineBytes || bytes>LIMITS.engineBytes) fail('engine_output_limit');
        if (/^id name /.test(line)) {
          version=line.slice(8).trim();
          if (!version || version.length>200) fail('malformed_uci');
        }
        waiting?.receive(line);
      } catch(error) { stop(error.code??'malformed_uci'); }
    };
    function waitFor(receive, commands, timeoutMs) {
      if (fault) return Promise.reject(fault);
      return new Promise((resolve,reject)=>{
        const timer=setTimeout(()=>stop('timeout'),timeoutMs);
        const finish=(callback,value)=>{clearTimeout(timer);waiting=null;callback(value);};
        waiting={reject:error=>finish(reject,error),receive:line=>{
          const result=receive(line);
          if (result!==undefined) finish(resolve,result);
        }};
        try { commands.forEach(command=>transport.send(command)); }
        catch { stop('engine_io'); }
      });
    }
    const ready = () => waitFor(line=>line==='readyok'?true:undefined,['isready'],startupTimeoutMs);
    async function search(candidate) {
      transport.send('ucinewgame');
      transport.send('setoption name Clear Hash');
      await ready();
      let latest=null;
      const context=request.context;
      const sign=(board.turn()==='w')===(context.learner_color==='white')?1:-1;
      const position=`position fen ${context.starting_fen}${context.history_uci.length?' moves '+context.history_uci.join(' '):''}`;
      const budget=[context.max_depth&&`depth ${context.max_depth}`,context.max_nodes&&`nodes ${context.max_nodes}`,
        context.max_time_ms&&`movetime ${context.max_time_ms}`].filter(Boolean).join(' ');
      return waitFor(line=>{
        const info=parseInfo(line,board,sign);
        if (info) {
          if (candidate && info.move_uci!==candidate) fail('search_context_mismatch');
          latest=info;observations.set(candidate??'*',info);
        }
        if (line.startsWith('bestmove ')) {
          const match=/^bestmove ([a-h][1-8][a-h][1-8][qrbn]?)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?$/.exec(line);
          if (!match) fail('malformed_uci');
          try { move(new Chess(board.fen({forceEnpassantSquare:true})),match[1]); } catch { fail('malformed_uci'); }
          if (candidate && match[1]!==candidate) fail('search_context_mismatch');
          if (!latest || latest.move_uci!==match[1]) return unavailable(match[1],'missing_score');
          return latest;
        }
      },[position,`go ${budget}${candidate?' searchmoves '+candidate:''}`],searchTimeoutMs);
    }
    let result, failure;
    const totalTimer=setTimeout(()=>stop('timeout'),totalTimeoutMs);
    signal?.addEventListener('abort',onAbort,{once:true});
    try {
      transport=launch({onLine,onError:()=>stop('engine_exit')});
      await waitFor(line=>line==='uciok'?true:undefined,['uci'],startupTimeoutMs);
      if (!version) fail('missing_engine_version');
      transport.send(`setoption name Threads value ${request.context.threads}`);
      transport.send(`setoption name Hash value ${request.context.hash_mb}`);
      transport.send('setoption name MultiPV value 1');
      await ready();
      let best=await search(null);
      const candidates=[], inconsistentCandidates=[];
      for (const candidate of request.candidates) candidates.push(await search(candidate));
      const versionMismatch=version!==request.context.engine_version;
      if (versionMismatch) {
        best=unavailable(best.move_uci,'engine_version_mismatch',best);
        for (let i=0;i<candidates.length;i++) candidates[i]=unavailable(candidates[i].move_uci,'engine_version_mismatch',candidates[i]);
      } else {
        for (let i=0;i<candidates.length;i++) {
          const candidate=candidates[i];
          const inconsistent=best.score?.kind==='centipawns' && candidate.score?.kind==='centipawns'
            && (board.turn()==='w'?1:-1)*(request.context.learner_color==='white'?1:-1)
              *(candidate.score.value-best.score.value)>0;
          if (inconsistent) inconsistentCandidates.push(candidate);
          if (candidate.score && (!best.score || candidate.depth!==best.depth || inconsistent)) {
            candidates[i]=unavailable(candidate.move_uci,inconsistent?'baseline_inconsistent':
              !best.score?'baseline_inconclusive':'comparison_depth_mismatch',candidate);
          }
        }
      }
      result={request_id:request.request_id,status:'ok',evidence:{interface_version:'0.2.0',evidence_id:request.request_id,
        context:{...request.context,engine_version:version},requested_candidates:request.candidates,best,candidates,unavailable_reason:null},
        diagnostics:{elapsed_ms:Math.ceil(performance.now()-started),cleanup:'verified',inconsistent_candidates:inconsistentCandidates}};
      if (!best.score || candidates.some(candidate=>!candidate.score)) result.status='inconclusive';
    } catch(error) {
      failure=error instanceof EvaluationError?error:new EvaluationError('engine_io');
      if (version) result={request_id:request.request_id,status:failure.code==='cancelled'?'cancelled':'unavailable',
        evidence:{interface_version:'0.2.0',evidence_id:request.request_id,context:{...request.context,engine_version:version},
          requested_candidates:request.candidates,best:null,candidates:request.candidates.map(uci=>unavailable(uci,failure.code,observations.get(uci))),
          unavailable_reason:failure.code},error:{code:failure.code},
        diagnostics:{elapsed_ms:Math.ceil(performance.now()-started),cleanup:'verified',
          incomplete_baseline:observations.has('*')?unavailable(observations.get('*').move_uci,failure.code,observations.get('*')):null}};
    } finally {
      clearTimeout(totalTimer);
      signal?.removeEventListener('abort',onAbort);
      waiting=null;
      try { await transport?.close(); }
      catch { quarantined=true; failure=new EvaluationError('cleanup_unverified'); result=null; }
      active=false;
    }
    if (!result) throw failure;
    if (Buffer.byteLength(JSON.stringify(result))>LIMITS.responseBytes) fail('response_limit');
    return freeze(result);
  }
  return Object.freeze({evaluate});
}
