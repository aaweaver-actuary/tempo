/** Independent audit overlay. Product source is unchanged; failures are preserved. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import fixture from '../fixtures/prefix-comparison/transition.json';
import structural from '../fixtures/prefix-comparison/structural.json';
import { prefixSourceSchema } from '../../app/domain/prefix-comparison';
import { prefixTransitionPlanSchema, prefixTransitionApplySchema, transitionSummary } from '../../app/domain/prefix-transition';
import { beginPrefixTransition, readPrefixTransition, recoverPrefixTransition, restorePrefixTransitionJournal, effectivePrefixTransitionScope, knownPrefixTransitions, type PrefixTransitionOperation } from '../../app/lib/prefix-transition-command';
const request = prefixTransitionApplySchema.parse(fixture.request);
const summary = transitionSummary(prefixTransitionPlanSchema.parse(fixture.ready), prefixSourceSchema.parse(structural.source), 'Audit repertoire');
const key = 'tempo-pending-prefix-transition-v1:rep';
const controllers: AbortController[] = [];
beforeEach(() => localStorage.clear());
afterEach(() => { controllers.forEach(controller => controller.abort()); controllers.length = 0; vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
function reserve(state: PrefixTransitionOperation['state'] = 'pending') {
 const operation = beginPrefixTransition('rep', request, summary);
 if (state !== 'pending') localStorage.setItem(key, JSON.stringify({...operation, state}));
 return {...operation, state};
}
function recover(retryBlocked = false, controller = new AbortController()) {
 controllers.push(controller); return recoverPrefixTransition('rep', {signal: controller.signal, retryBlocked});
}
function receipt(operation: PrefixTransitionOperation, state = 'complete') {
 return Response.json({operation_id: operation.operationId, state,
  ...(state === 'complete' ? {response: {...fixture.result, operation_id: operation.operationId},
   transition: {state: 'complete', graph_generation: fixture.result.graph_generation, queue_date: fixture.result.queue_date, recovery: null}} : {}),
  ...(state === 'blocked' ? {last_error: {detail: {code: 'publication_recovery_required', message: 'Restore publisher and retry.'}},
   transition: {state: 'recovery_required', graph_generation: 2, queue_date: null, recovery: 'Retry this operation; activated graph remains committed.'}} : {})});
}
function controlledDeadline() {
 vi.useFakeTimers();
 vi.spyOn(AbortSignal, 'timeout').mockImplementation(milliseconds => {
  expect(milliseconds).toBe(30_000);
  const deadlineController = new AbortController(); controllers.push(deadlineController);
  setTimeout(() => deadlineController.abort(new DOMException('Recovery deadline', 'TimeoutError')), milliseconds);
  return deadlineController.signal;
 });
}
function hungRequest(signal: AbortSignal) {
 return new Promise<Response>((_resolve, reject) => { signal.addEventListener('abort', () => reject(signal.reason), {once:true}); if(signal.aborted) reject(signal.reason); });
}
function identityUnchanged(original: PrefixTransitionOperation) {
 const stored = readPrefixTransition('rep')!;
 expect(stored.operationId).toBe(original.operationId); expect(stored.repertoireId).toBe(original.repertoireId);
 expect(stored.request).toEqual(original.request); expect(stored.summary).toEqual(original.summary);
}
it('audit187_status_404_unavailable_never_replays_apply', async () => {
 const operation = reserve();
 const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, {status:404}));
 vi.stubGlobal('fetch', fetcher); expect((await recover())?.state).toBe('transport_unknown');
 expect(fetcher).toHaveBeenCalledOnce(); expect(readPrefixTransition('rep')?.message).toMatch(/unavailable/);
 identityUnchanged(operation);
});
it('audit187_status_503_suppresses_delivery_and_next_recovery_reads_first', async () => {
 const operation = reserve(); const fetcher = vi.fn().mockResolvedValueOnce(Response.json({detail:'database unavailable'}, {status:503})).mockResolvedValueOnce(receipt(operation));
 vi.stubGlobal('fetch', fetcher); expect((await recover())?.state).toBe('transport_unknown'); identityUnchanged(operation);
 expect(fetcher).toHaveBeenCalledOnce(); expect((await recover())?.state).toBe('published');
 expect(fetcher.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
});
it('audit187_status_foreground_503_waits_without_delivery', async () => {
 controlledDeadline(); const operation = reserve();
 const fetcher = vi.fn().mockResolvedValueOnce(Response.json({detail:'Waiting for foreground activity'}, {status:503,headers:{'Retry-After':'1'}})).mockResolvedValueOnce(receipt(operation));
 vi.stubGlobal('fetch', fetcher); const recovering = recover(); await vi.advanceTimersByTimeAsync(1000);
 expect((await recovering)?.state).toBe('published'); expect(fetcher).toHaveBeenCalledTimes(2);
 expect(fetcher.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true);
});
it.each([200,202,409,503,'lost'] as const)('audit187_apply_%s_waits_for_authoritative_receipt', async response => {
 const operation = reserve(); let admitted = false; let release!: (response:Response)=>void;
 const fetcher = vi.fn(async (_url, options?: RequestInit) => {
  if(options?.method === 'POST') { admitted=true; if(response === 'lost') throw new TypeError('lost acknowledgment'); return Response.json({untrusted:'apply response'}, {status:response}); }
  if(!admitted) return receipt(operation,'unknown');
  return new Promise<Response>(resolve => { release=resolve; });
 });
 vi.stubGlobal('fetch',fetcher); const recovering = recover(); await vi.waitFor(() => expect(release).toBeTypeOf('function'));
 expect(readPrefixTransition('rep')?.state).toBe('transport_unknown'); expect(readPrefixTransition('rep')?.result).toBeUndefined();
 release(receipt(operation)); expect((await recovering)?.state).toBe('published'); identityUnchanged(operation);
 const delivery=fetcher.mock.calls.find(([,options])=>options?.method==='POST')![1]!;
 expect(new Headers(delivery.headers).get('Idempotency-Key')).toBe(operation.operationId); expect(JSON.parse(String(delivery.body))).toEqual(request);
});
it('audit187_apply_409_with_failed_receipt_surfaces_stale_plan', async () => {
 const operation=reserve(); const fetcher=vi.fn().mockResolvedValueOnce(receipt(operation,'unknown'))
 .mockResolvedValueOnce(Response.json({detail:{code:'stale_plan'}},{status:409}))
 .mockResolvedValueOnce(Response.json({operation_id:operation.operationId,state:'failed',error:{detail:{code:'stale_plan',message:'Approve a fresh plan.'}}}));
 vi.stubGlobal('fetch',fetcher); expect((await recover())?.state).toBe('stale'); expect(readPrefixTransition('rep')?.failureCode).toBe('stale_plan'); identityUnchanged(operation);
});
it.each([409,503,'lost'] as const)('audit187_apply_%s_without_receipt_stays_unknown_and_replays_same_identity_on_user_action', async response => {
 const operation=reserve(); const fetcher=vi.fn(async (_url,options?:RequestInit) => {
  if(options?.method==='POST') {if(response==='lost') throw new TypeError('lost'); return Response.json({detail:'Admission rejected'},{status:response});}
  return receipt(operation,'unknown');
 }); vi.stubGlobal('fetch',fetcher);
 for(let action=0;action<3;action++) {expect((await recover())?.state).toBe('transport_unknown'); identityUnchanged(operation);}
 const deliveries=fetcher.mock.calls.filter(([,options])=>options?.method==='POST'); expect(deliveries).toHaveLength(3);
 for(const [,options] of deliveries) {expect(new Headers(options?.headers).get('Idempotency-Key')).toBe(operation.operationId); expect(JSON.parse(String(options?.body))).toEqual(request);}
});
it.each([409,503,'lost',202] as const)('audit187_retry_%s_can_converge_to_completed_original_operation', async response => {
 const operation=reserve('blocked'); let statusReads=0; const fetcher=vi.fn(async (_url,options?:RequestInit)=> {
  if(options?.method==='POST') {if(response==='lost') throw new TypeError('lost'); return Response.json({state:'blocked'},{status:response});}
  return receipt(operation,++statusReads===1?'blocked':'complete');
 }); vi.stubGlobal('fetch',fetcher); expect((await recover(true))?.state).toBe('published'); identityUnchanged(operation);
 expect(fetcher.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);
 expect(fetcher.mock.calls[0][1]?.method ?? 'GET').toBe('GET');
 expect(String(fetcher.mock.calls[1][0])).toContain(`/operations/${operation.operationId}/retry`);
 expect(fetcher.mock.calls.some(([url])=>String(url).endsWith('/apply'))).toBe(false);
});
it.each([409,503,'lost',202] as const)('audit187_retry_%s_persistent_blockage_retains_identity_with_truthful_feedback', async response => {
 controlledDeadline(); const operation=reserve('blocked'); const fetcher=vi.fn(async (_url,options?:RequestInit) => {
  if(options?.method==='POST') {if(response==='lost') throw new TypeError('lost'); return Response.json({detail:'Retry queue unavailable'}, {status:response});}
  return receipt(operation,'blocked');
 }); vi.stubGlobal('fetch',fetcher); let settled=false; const recovering=recover(true).then(value=>{settled=true;return value;});
 await vi.advanceTimersByTimeAsync(0);
 if(response===409||response===503) {
   expect(settled).toBe(true); expect(fetcher).toHaveBeenCalledTimes(3);
   expect((await recovering)?.message).toContain(`HTTP ${response}`);
 } else {
   await vi.advanceTimersByTimeAsync(29_999); expect(settled).toBe(false);
   await vi.advanceTimersByTimeAsync(1); expect((await recovering)?.message).toMatch(/timed out/);
 }
 expect((await recovering)?.state).toBe('blocked'); identityUnchanged(operation);
 expect(fetcher.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);
 const nextFetcher=vi.fn<typeof fetch>(async()=>receipt(operation)); vi.stubGlobal('fetch',nextFetcher); expect((await recover())?.state).toBe('published'); expect(nextFetcher.mock.calls[0][1]?.method).toBeUndefined();
});
it.each(['pending','status','apply','blocked'] as const)('audit187_timeout_%s_preserves_last_state_and_next_action_reads_status', async scenario => {
 controlledDeadline(); const operation=reserve(scenario==='blocked'?'blocked':'pending');
 const fetcher=vi.fn(async (_url,options?:RequestInit)=> {
  if(scenario==='status'||options?.method==='POST') return hungRequest(options!.signal!);
  return receipt(operation,scenario==='apply'?'unknown':scenario==='blocked'?'blocked':'pending');
 }); vi.stubGlobal('fetch',fetcher); const recovering=recover(scenario==='blocked'); await vi.advanceTimersByTimeAsync(30_000);
 const result=await recovering; expect(result?.state).toBe(scenario==='apply'?'transport_unknown':scenario==='blocked'?'blocked':'pending'); identityUnchanged(operation);
 expect(result?.state).not.toBe('failed'); const nextFetcher=vi.fn<typeof fetch>(async()=>receipt(operation)); vi.stubGlobal('fetch',nextFetcher);
 expect((await recover())?.state).toBe('published'); expect(nextFetcher.mock.calls[0][1]?.method).toBeUndefined();
});
it('audit187_abort_during_post_then_reload_recovers_original_request', async () => {
 const operation=reserve(); const controller=new AbortController();
 const fetcher=vi.fn(async (_url,options?:RequestInit)=>options?.method==='POST'?hungRequest(options.signal!):receipt(operation,'unknown'));
 vi.stubGlobal('fetch',fetcher); const recovering=recover(false,controller); const rejection=expect(recovering).rejects.toThrow();
 await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2)); controller.abort(); await rejection; identityUnchanged(operation);
 const next=vi.fn<typeof fetch>(async()=>receipt(operation)); vi.stubGlobal('fetch',next); expect((await recover())?.state).toBe('published'); expect(next).toHaveBeenCalledOnce();
});
it('audit187_concurrent_recovery_can_duplicate_delivery_but_retains_one_logical_identity', async () => {
 const operation=reserve(); const fetcher=vi.fn<typeof fetch>(async()=>receipt(operation,'unknown')); vi.stubGlobal('fetch',fetcher);
 await Promise.all([recover(),recover()]); const deliveries=fetcher.mock.calls.filter(([,options])=>options?.method==='POST'); expect(deliveries).toHaveLength(2);
 for(const [,options] of deliveries) expect(new Headers(options?.headers).get('Idempotency-Key')).toBe(operation.operationId); identityUnchanged(operation);
});
it.each(['get','set','quota'] as const)('audit187_storage_%s_failure_at_reservation_prevents_delivery', failure => {
 const fetcher=vi.fn(); vi.stubGlobal('fetch',fetcher);
 vi.spyOn(Storage.prototype,failure==='get'?'getItem':'setItem').mockImplementation(()=>{throw new DOMException('Storage unavailable',failure==='quota'?'QuotaExceededError':'SecurityError');});
 expect(()=>reserve()).toThrow(); expect(fetcher).not.toHaveBeenCalled();
});
it.each(['malformed','version','repertoire','routes'] as const)('audit187_saved_%s_mismatch_blocks_recovery_without_delivery', async mismatch => {
 const operation=reserve(); localStorage.setItem(key,mismatch==='malformed'?'invalid':JSON.stringify({...operation,
  ...(mismatch==='version'?{version:2}:{}),...(mismatch==='repertoire'?{repertoireId:'other'}:{}),...(mismatch==='routes'?{summary:{...summary,routes:[]}}:{})}));
 const fetcher=vi.fn();vi.stubGlobal('fetch',fetcher);await expect(recover()).rejects.toThrow(/unreadable/); expect(fetcher).not.toHaveBeenCalled();
});
it.each(['get','set'] as const)('audit187_storage_%s_unavailable_after_reservation_prevents_undelivered_post', async failure => {
 reserve(); const fetcher=vi.fn<typeof fetch>(async()=>receipt(readPrefixTransition('rep')!,'unknown'));vi.stubGlobal('fetch',fetcher);
 vi.spyOn(Storage.prototype,failure==='get'?'getItem':'setItem').mockImplementation(()=>{throw new DOMException('Storage unavailable','SecurityError');});
 await expect(recover()).rejects.toThrow(); expect(fetcher.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false);
});
it('audit187_journal_deleted_between_status_and_delivery_suppresses_post', async () => {
 const operation=reserve(); let release!:(response:Response)=>void;
 const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{release=resolve;}));vi.stubGlobal('fetch',fetcher);
 const recovering=recover();localStorage.removeItem(key);release(receipt(operation,'unknown'));expect((await recovering)?.journalMissing).toBe(true);expect(fetcher).toHaveBeenCalledOnce();
});
it('audit187_journal_deleted_after_admission_cannot_save_matching_completion', async () => {
 const operation=reserve();let release!:(response:Response)=>void;
 const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{release=resolve;}));vi.stubGlobal('fetch',fetcher);
 const recovering=recover();localStorage.removeItem(key);release(receipt(operation));expect((await recovering)?.state).toBe('published');
 expect(readPrefixTransition('rep')?.operationId).toBe(operation.operationId); expect((await recover())?.state).toBe('published'); expect(fetcher).toHaveBeenCalledOnce();
});
it('audit187_write_unavailable_after_admission_retains_journal_and_can_later_verify', async () => {
 const operation=reserve();const fetcher=vi.fn<typeof fetch>(async()=>receipt(operation));vi.stubGlobal('fetch',fetcher);
 const unavailable=vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new DOMException('Quota','QuotaExceededError');});
 await expect(recover()).rejects.toThrow(/Quota/);unavailable.mockRestore();identityUnchanged(operation);expect((await recover())?.state).toBe('published');
});
// Normative safety assertions intentionally fail on the reviewed product. Do not "fix" these tests.
it('audit187_DEFECT_B_blocked_retry_must_check_authoritative_status_before_post', async () => {
 const operation=reserve('blocked');const fetcher=vi.fn<typeof fetch>(async()=>receipt(operation));vi.stubGlobal('fetch',fetcher);
 await recover(true);expect(fetcher.mock.calls[0][1]?.method ?? 'GET').toBe('GET');
});
it('audit187_DEFECT_timeout_must_explain_ended_recovery_attempt', async () => {
 controlledDeadline();const operation=reserve();vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(operation,'pending')));
 const recovering=recover();await vi.advanceTimersByTimeAsync(30_000);expect((await recovering)?.message).toMatch(/timed out|deadline|check status again/i);
});
it('audit187_DEFECT_D_summary_cannot_omit_its_own_repertoire_from_edit_guard', () => {
 const operation=reserve();localStorage.setItem(key,JSON.stringify({...operation,summary:{...summary,affectedRepertoireIds:[]}}));
 expect(()=>readPrefixTransition('rep')).toThrow(/unreadable|scope|repertoire/i);
});
it('audit187_storage_read_unavailable_after_admission_can_recover_when_restored', async () => {
 const operation=reserve();vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(operation)));
 const inaccessible=vi.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new DOMException('Storage unavailable','SecurityError');});
 await expect(recover()).rejects.toThrow(/Storage unavailable/);inaccessible.mockRestore();identityUnchanged(operation);expect((await recover())?.state).toBe('published');
});
it('audit187_DEFECT_A_lost_journal_during_unknown_delivery_must_not_allow_new_identity', async () => {
 const original=reserve();const controller=new AbortController();
 const fetcher=vi.fn(async (_url,options?:RequestInit)=>options?.method==='POST'?hungRequest(options.signal!):receipt(original,'unknown'));
 vi.stubGlobal('fetch',fetcher);const recovering=recover(false,controller);const rejection=expect(recovering).rejects.toThrow();
 await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));localStorage.removeItem(key);
 const second=beginPrefixTransition('rep',request,summary);controller.abort();await rejection;
 expect(second.operationId).toBe(original.operationId);
});

function browserLocks() {
 vi.stubGlobal('navigator', {locks: {request: async (_name: string, _options: unknown, callback: () => unknown) => callback()}});
}
it.each(['queued','executing','retrying','pending'])('audit187_retry_does_not_dispatch_for_authoritative_%s',async state=>{
 controlledDeadline();const operation=reserve('blocked');let reads=0;
 const fetcher=vi.fn<typeof fetch>(async()=>receipt(operation,++reads===1?state:'complete'));vi.stubGlobal('fetch',fetcher);
 const recovery=recover(true);await vi.advanceTimersByTimeAsync(1000);expect((await recovery)?.state).toBe('published');
 expect(fetcher.mock.calls.every(([,options])=>options?.method!=='POST')).toBe(true);
});
it.each([409,503])('audit187_rejected_retry_%s_keeps_polling_concurrent_active_progress',async rejection=>{
 controlledDeadline();const operation=reserve('blocked');let reads=0;
 const fetcher=vi.fn<typeof fetch>(async(_url,options)=>options?.method==='POST'?Response.json({detail:'not admitted'},{status:rejection}):receipt(operation,++reads===1?'blocked':reads===2?'executing':'complete'));
 vi.stubGlobal('fetch',fetcher);const recovery=recover(true);await vi.advanceTimersByTimeAsync(1000);
 expect((await recovery)?.state).toBe('published');expect(fetcher.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);
});
it('audit187_missing_journal_explicit_restoration_preserves_original_request_and_reads_first',async()=>{
 browserLocks();const original=reserve();localStorage.removeItem(key);
 const statusOnly=vi.fn<typeof fetch>(async()=>receipt(original,'unknown'));vi.stubGlobal('fetch',statusOnly);
 expect((await recover())?.journalMissing).toBe(true);expect(localStorage.getItem(key)).toBeNull();
 expect(statusOnly).toHaveBeenCalledOnce();
 const restored=await restorePrefixTransitionJournal('rep',original.operationId,new AbortController().signal);
 expect(restored?.operationId).toBe(original.operationId);expect(restored?.request).toEqual(original.request);
 let posted=false;const delivery=vi.fn<typeof fetch>(async(_url,options)=>{
   if(options?.method==='POST'){posted=true;return Response.json({},{status:202});}
   return receipt(original,posted?'complete':'unknown');
 });vi.stubGlobal('fetch',delivery);expect((await recover())?.state).toBe('published');
 expect(delivery.mock.calls[0][1]?.method).not.toBe('POST');identityUnchanged(original);
});
it('audit187_restore_completed_operation_does_not_resurrect_cleared_journal',async()=>{
 browserLocks();const original=reserve();localStorage.removeItem(key);vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(original)));
 expect((await restorePrefixTransitionJournal('rep',original.operationId,new AbortController().signal))?.state).toBe('published');
 expect(localStorage.getItem(key)).toBeNull();
});
it('audit187_restore_conflicting_record_never_overwrites_new_identity',async()=>{
 const original=reserve();localStorage.removeItem(key);const newer={...original,operationId:'newer'};
 vi.stubGlobal('navigator',{locks:{request:async(_name:string,_options:unknown,callback:()=>unknown)=>{localStorage.setItem(key,JSON.stringify(newer));return callback();}}});
 vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(original,'unknown')));
 await expect(restorePrefixTransitionJournal('rep',original.operationId,new AbortController().signal)).rejects.toThrow(/already exists/);
 expect(JSON.parse(localStorage.getItem(key)!).operationId).toBe('newer');expect(knownPrefixTransitions()[0].operationId).toBe(original.operationId);
});
it('audit187_restore_without_web_locks_fails_closed',async()=>{
 vi.stubGlobal('navigator',{});const original=reserve();localStorage.removeItem(key);
 await expect(restorePrefixTransitionJournal('rep',original.operationId,new AbortController().signal)).rejects.toThrow(/Web Locks/);
 expect(beginPrefixTransition('rep',request,summary).operationId).toBe(original.operationId);expect(localStorage.getItem(key)).toBeNull();
});
it('audit187_restore_quota_failure_keeps_known_identity_and_guard',async()=>{
 browserLocks();const original=reserve();localStorage.removeItem(key);vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(original,'unknown')));
 vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new DOMException('Quota','QuotaExceededError');});
 await expect(restorePrefixTransitionJournal('rep',original.operationId,new AbortController().signal)).rejects.toThrow(/Quota/);
 expect(readPrefixTransition('rep')?.operationId).toBe(original.operationId);expect(effectivePrefixTransitionScope(readPrefixTransition('rep')!)).toContain('rep');
});
it.each(['empty','depths','duplicate'])('audit187_invalid_%s_journal_fails_closed',kind=>{
 const original=reserve();localStorage.setItem(key,kind==='empty'?'':JSON.stringify({...original,request:{...original.request,
 ...(kind==='depths'?{candidate_depths:{}}:{selected_line_ids:[...original.request.selected_line_ids,original.request.selected_line_ids[0]]})}}));
 expect(()=>readPrefixTransition('rep')).toThrow(/unreadable/);expect(knownPrefixTransitions()[0].operationId).toBe(original.operationId);
});
it('audit187_missing_journal_keeps_shared_scope_and_new_unrelated_operations_work',()=>{
 const original=beginPrefixTransition('rep',request,{...summary,affectedRepertoireIds:['rep','shared']});localStorage.removeItem(key);
 expect(()=>beginPrefixTransition('shared',request,{...summary,affectedRepertoireIds:['shared']})).toThrow(/Resolve/);
 expect(beginPrefixTransition('rep',request,summary).operationId).toBe(original.operationId);
 expect(beginPrefixTransition('other',request,{...summary,affectedRepertoireIds:['other']}).operationId).not.toBe(original.operationId);
});
it('audit187_failed_completion_write_retains_verified_in_memory_evidence',async()=>{
 const original=reserve();vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(original)));
 vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new DOMException('Quota','QuotaExceededError');});
 await expect(recover()).rejects.toThrow(/Quota/);
 expect(knownPrefixTransitions()[0].state).toBe('published');expect(knownPrefixTransitions()[0].result?.operation_id).toBe(original.operationId);
});
