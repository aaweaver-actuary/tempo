// Only the controlled test build includes this entry point. All computation and
// cancellation below use the production modules and actual browser Workers.
import { runStudyTask } from '../../../app/lib/background-study';
import { installStudyInteractionPriority, setStudyPriorityScope, studyInteractionActive } from '../../../app/lib/study-priority';
import { tempoPerformanceTimings } from '../../../app/lib/performance';
import type { AnalysisLine } from '../../../app/types';
const nativeWorker = window.Worker;
const events: Array<{kind:string;at:number}> = [];
window.Worker = class extends nativeWorker {
  constructor(url: string | URL, options?: WorkerOptions) {
    super(url,options);
    if(options?.name === 'tempo-optional-preparation') {
      events.push({kind:'created',at:performance.now()});
      this.addEventListener('message', ({data}) => { if(data.state==='running') events.push({kind:'running',at:performance.now()}); });
    }
  }
  terminate() { events.push({kind:'terminated',at:performance.now()}); super.terminate(); }
};
setStudyPriorityScope(true);
installStudyInteractionPriority();
const startFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
let controller: AbortController;
let settled = 'none';
const proof = {
  events, active:studyInteractionActive, timings:tempoPerformanceTimings,
  start(count=60_000) {
    controller = new AbortController(); settled='pending';
    const lines = Array.from({length:count},(_,index)=>({id:'line-'+index,repertoireId:'proof',repertoireName:'Proof',title:'Line '+index,
      side:'white',startingFen:startFen,moves:['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6']})) as AnalysisLine[];
    void runStudyTask({kind:'index',lines},controller.signal).then(()=>{settled='complete';},error=>{settled=error.name;});
  },
  cancel(){ controller.abort(); }, settled:()=>settled,
  async essential(){ const started=performance.now(); await runStudyTask({kind:'queue',payload:{cards:[],count:0}}); return performance.now()-started; },
};
Object.assign(window,{priorityProof:proof});
