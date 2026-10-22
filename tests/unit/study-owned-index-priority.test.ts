// @vitest-environment node
import {afterEach,expect,it,vi} from 'vitest';
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
async function fixture() {
  vi.resetModules();
  const workers: FakeWorker[]=[];
  class FakeWorker {
    onmessage: ((event:{data:unknown})=>void)|null=null;onerror=null;onmessageerror=null;
    terminate=vi.fn();postMessage=vi.fn();
    constructor(){workers.push(this);}
    reply(index:number,result:unknown=true){this.onmessage?.({data:{id:this.postMessage.mock.calls[index][0].id,result}});}
  }
  vi.stubGlobal('Worker',FakeWorker);
  return {workers,...await import('../../app/lib/owned-study-index')};
}
it('study_priority_cancelled_replacement_preserves_confirmed_index_and_ignores_stale_replies',async()=>{
  const {workers,runOwnedStudyIndex}=await fixture();
  const original={kind:'initializePositionIndex' as const,repertoireId:'rep',revision:1,lines:[]};
  const first=runOwnedStudyIndex(original);workers[0].reply(0);await first;
  const controller=new AbortController();
  const replacement=runOwnedStudyIndex({...original,revision:2},controller.signal).catch(error=>error);
  const late=workers[0].onmessage;controller.abort();
  expect(await replacement).toMatchObject({name:"AbortError"});
  const query=runOwnedStudyIndex({kind:'findPositionMatches',repertoireId:'rep',revision:1,fen:'fixture'});
  expect(workers[1].postMessage.mock.calls[0][0].task).toEqual(original);
  late?.({data:{id:workers[0].postMessage.mock.calls[1][0].id,result:'obsolete'}});
  workers[1].reply(0);workers[1].reply(1,['confirmed']);
  await expect(query).resolves.toEqual(['confirmed']);
  await runOwnedStudyIndex({kind:'releasePositionIndex',repertoireId:'rep',revision:1});
});
it('study_priority_owned_index_computation_is_serial_and_idle_confirmed_workers_survive_interaction',async()=>{
  const {workers,runOwnedStudyIndex}=await fixture();
  const first=runOwnedStudyIndex({kind:'initializePositionIndex',repertoireId:'first',revision:1,lines:[]});
  const second=runOwnedStudyIndex({kind:'initializePositionIndex',repertoireId:'second',revision:1,lines:[]});
  expect(workers).toHaveLength(1);workers[0].reply(0);await first;
  expect(workers).toHaveLength(2);workers[1].reply(0);await second;
  const priority=await import('../../app/lib/study-priority');priority.markStudyInteraction();
  expect(workers[0].terminate).not.toHaveBeenCalled();expect(workers[1].terminate).not.toHaveBeenCalled();
  await runOwnedStudyIndex({kind:'releasePositionIndex',repertoireId:'first',revision:1});
  await runOwnedStudyIndex({kind:'releasePositionIndex',repertoireId:'second',revision:1});
});
