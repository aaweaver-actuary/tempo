import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';

const pause=milliseconds=>new Promise(resolve=>setTimeout(resolve,milliseconds));
const inspectEnvironment={...process.env,TZ:'UTC',LC_ALL:'C'};

function inspectProcess(pid) {
  try {
    const startTime=execFileSync('ps',['-p',String(pid),'-o','lstart='],{encoding:'utf8',stdio:'pipe',env:inspectEnvironment}).trim();
    const environment=execFileSync('ps',['eww','-p',String(pid),'-o','command='],{encoding:'utf8',stdio:'pipe',env:inspectEnvironment,maxBuffer:1024*1024});
    return {startTime,environment};
  } catch(error) {
    if(error.status===1&&!String(error.stdout??'').trim()&&!String(error.stderr??'').trim())return undefined;
    throw error;
  }
}

function containsEnvironment(environment,name,value) {
  const marker=` ${name}=${value}`;
  const position=(' '+environment).indexOf(marker);
  return position>=0&&(/\s/.test((' '+environment)[position+marker.length]??' ') );
}

/** A dead controller is insufficient: match its recorded child and capability. */
export async function reconcileRecordedHostProcesses(records,{directory,token},{graceMilliseconds=10000,killMilliseconds=5000}={}) {
  const results=[];
  for(const record of records) {
    assert.equal(record.directory,directory,'Qualification process directory changed');
    assert.equal(record.token,token,'Qualification process lease changed');
    assert.match(record.nonce,/^[0-9a-f]{32}$/,'Qualification process nonce is missing');
    if(record.exited===true){results.push({pid:record.pid,absent:true,confirmed_exit:true});continue;}
    assert(Number.isInteger(record.pid)&&record.pid>0,'Process creation is unverified; retain its journal and reconcile the recorded nonce');
    const verify=()=>{
      const current=inspectProcess(record.pid);
      if(!current)return false;
      assert(record.start_time&&current.startTime===record.start_time,'Qualification process start identity changed; do not signal');
      for(const [name,value]of Object.entries({TEMPO_SATURATION_DIRECTORY:directory,TEMPO_DEVELOPMENT_BUDGET_TOKEN:token,TEMPO_QUALIFICATION_PROCESS_NONCE:record.nonce}))
        assert(containsEnvironment(current.environment,name,value),'Qualification process ownership changed; do not signal');
      return true;
    };
    const result={pid:record.pid,signals:[],absent:false};
    for(const [signal,milliseconds]of [['SIGTERM',graceMilliseconds],['SIGKILL',killMilliseconds]]) {
      if(!verify())break;
      try{process.kill(record.pid,signal);result.signals.push(signal);}catch(error){if(error.code!=='ESRCH')throw error;}
      // Allow the controller or init to reap the exit before inspecting an
      // environment that may already have disappeared with the process.
      await pause(25);
      const deadline=performance.now()+milliseconds;
      while(verify()&&performance.now()<deadline)await pause(25);
    }
    assert(!verify(),'Owned qualification process remains; retain resource ownership');
    result.absent=true;results.push(result);
  }
  return results;
}
