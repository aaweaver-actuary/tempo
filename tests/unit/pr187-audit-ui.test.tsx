import { installPrefixTransitionTestLocks } from "./support/prefix-transition-locks";
/** Independent UI audit overlay; product snapshot remains unchanged. */
import {beforeEach,afterEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {PrefixTransitionPanel} from '../../app/components/prefix-transition-panel';
import RepertoireView from '../../app/views/repertoire_view';
import {beginPrefixTransition, readPrefixTransition} from '../../app/lib/prefix-transition-command';
import {prefixTransitionApplySchema,prefixTransitionPlanSchema,transitionSummary} from '../../app/domain/prefix-transition';
import {prefixSourceSchema} from '../../app/domain/prefix-comparison';
import transition from '../fixtures/prefix-comparison/transition.json';
import structural from '../fixtures/prefix-comparison/structural.json';
vi.mock('../../app/utils/local',()=>({usesLocalApi:()=>true}));
vi.mock('../../app/components/chessboard',()=>({Chessboard:()=> <div/>}));
vi.mock('../../app/lib/workspace-data',async importOriginal=>({...await importOriginal<typeof import('../../app/lib/workspace-data')>(),readWorkspaceResponse:(input:string)=>fetch(input)}));
const request=prefixTransitionApplySchema.parse(transition.request);
const summary=transitionSummary(prefixTransitionPlanSchema.parse(transition.ready),prefixSourceSchema.parse(structural.source),'Audit repertoire');
const selectedIds:string[]=[];
const key='tempo-pending-prefix-transition-v1:rep';
const deadlines:AbortController[]=[];
afterEach(()=>{cleanup();deadlines.forEach(controller=>controller.abort());deadlines.length=0;vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();localStorage.clear();});
async function reserve(state='pending') {const operation=await beginPrefixTransition('rep',request,summary);localStorage.setItem(key,JSON.stringify({...operation,state}));return operation;}
function receipt(operationId:string,state='complete') {return Response.json({operation_id:operationId,state,
 ...(state==='complete'?{response:{...transition.result,operation_id:operationId},transition:{state:'complete',graph_generation:2,queue_date:transition.result.queue_date,recovery:null}}:{}),
 ...(state==='blocked'?{last_error:{detail:{code:'publication_recovery_required',message:'Restore queue publisher and retry.'}},transition:{state:'recovery_required',graph_generation:2,queue_date:null,recovery:'Retry the original operation; activated graph remains committed.'}}:{})});}
function panel(){return render(<PrefixTransitionPanel repertoireId='rep' repertoireName='Audit repertoire' source={null} selectedIds={selectedIds} depth={null} onBack={vi.fn()} onPublished={vi.fn(async()=>{})}/>);}
function library(){return render(<RepertoireView imported={[]} onImport={vi.fn()} onBrowse={vi.fn()} onResolveGap={vi.fn()} onShowGamesAtPosition={vi.fn()} onRepair={vi.fn()} onDeleteLocal={vi.fn()} onRenameLocal={vi.fn()} onQueueChanged={vi.fn()} onTrain={vi.fn()}/>);}
function libraryFetch(){vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>Response.json({repertoires:[{id:'rep',name:'Audit repertoire',source_name:'audit.pgn',line_count:2,card_count:2,due_count:0}]})));}
function fakeDeadline(){vi.useFakeTimers();vi.spyOn(AbortSignal,'timeout').mockImplementation(milliseconds=>{expect(milliseconds).toBe(30_000);const controller=new AbortController();deadlines.push(controller);setTimeout(()=>controller.abort(new DOMException('Deadline','TimeoutError')),milliseconds);return controller.signal;});}
it.each(['online','focus','visibilitychange'] as const)('audit187_%s_wakeup_rechecks_status_without_new_identity',async event=>{
 const operation=await reserve();const fetcher=vi.fn().mockResolvedValueOnce(Response.json({detail:'offline'},{status:503})).mockResolvedValueOnce(receipt(operation.operationId));vi.stubGlobal('fetch',fetcher);panel();
 await screen.findByRole('heading',{name:'Delivery unconfirmed'});expect(fetcher).toHaveBeenCalledOnce();
 fireEvent(event==='visibilitychange'?document:window,new Event(event));await screen.findByRole('heading',{name:'Prefix transition published'});
 expect(fetcher).toHaveBeenCalledTimes(2);expect(fetcher.mock.calls.every(([,options])=>options?.method!=='POST')).toBe(true);expect(readPrefixTransition('rep')?.request).toEqual(request);
});
it('audit187_hidden_or_offline_wakeup_does_not_deliver',async()=>{
 const operation=await reserve();const fetcher=vi.fn().mockResolvedValue(Response.json({detail:'offline'},{status:503}));vi.stubGlobal('fetch',fetcher);panel();await screen.findByRole('heading',{name:'Delivery unconfirmed'});
 vi.spyOn(document,'visibilityState','get').mockReturnValue('hidden');fireEvent.focus(window);await act(async()=>{});expect(fetcher).toHaveBeenCalledOnce();
 vi.spyOn(document,'visibilityState','get').mockReturnValue('visible');vi.spyOn(navigator,'onLine','get').mockReturnValue(false);fireEvent(window,new Event('online'));await act(async()=>{});expect(fetcher).toHaveBeenCalledOnce();
 expect(readPrefixTransition('rep')?.operationId).toBe(operation.operationId);
});
it('audit187_remount_and_repeated_status_clicks_keep_original_operation',async()=>{
 const operation=await reserve();let release!:(response:Response)=>void;const fetcher=vi.fn().mockResolvedValueOnce(Response.json({detail:'offline'},{status:503})).mockImplementation(()=>new Promise<Response>(resolve=>{release=resolve;}));vi.stubGlobal('fetch',fetcher);
 const first=panel();await screen.findByRole('heading',{name:'Delivery unconfirmed'});first.unmount();panel();await waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));
 await act(async()=>release(Response.json({detail:'offline'},{status:503})));const check=await screen.findByRole('button',{name:'Check status'});
 fireEvent.click(check);fireEvent.click(check);fireEvent.focus(window);expect(fetcher).toHaveBeenCalledTimes(3);
 await act(async()=>release(receipt(operation.operationId)));await screen.findByRole('heading',{name:'Prefix transition published'});expect(readPrefixTransition('rep')?.operationId).toBe(operation.operationId);
});
it('audit187_retry_503_restores_controls_promptly_and_explains_rejection',async()=>{
 const operation=await reserve('blocked');const fetcher=vi.fn(async(_url,options?:RequestInit)=>options?.method==='POST'?Response.json({detail:'Save queue unavailable; blocked operation remains durable'},{status:503}):receipt(operation.operationId,'blocked'));
 vi.stubGlobal('fetch',fetcher);panel();
 const retry=await screen.findByRole('button',{name:'Retry publication recovery'});fireEvent.click(retry);
 await screen.findByText(/HTTP 503/);await waitFor(()=>expect((screen.getByRole('button',{name:'Retry publication recovery'}) as HTMLButtonElement).disabled).toBe(false));
 expect((screen.getByRole('button',{name:'Check status'}) as HTMLButtonElement).disabled).toBe(false);
 expect(readPrefixTransition('rep')?.state).toBe('blocked');expect(readPrefixTransition('rep')?.operationId).toBe(operation.operationId);
});
it('audit187_pending_timeout_leaves_check_status_available_without_success',async()=>{
 fakeDeadline();const operation=await reserve();vi.stubGlobal('fetch',vi.fn<typeof fetch>(async()=>receipt(operation.operationId,'pending')));panel();
 await act(async()=>{await vi.advanceTimersByTimeAsync(30_000);});expect(screen.queryByRole('heading',{name:'Prefix transition published'})).toBeNull();
 expect((screen.getByRole('button',{name:'Check status'}) as HTMLButtonElement).disabled).toBe(false);expect(screen.queryByText(/Checking the original durable operation/)).toBeNull();
 expect(screen.getByRole('heading',{name:'Prefix transition in progress'})).toBeDefined();
});
it.each(['unreadable','getItem'] as const)('audit187_storage_%s_pauses_parent_structural_edits',async failure=>{
 await reserve();if(failure==='unreadable')localStorage.setItem(key,'broken');else vi.spyOn(Storage.prototype,'getItem').mockImplementation(key=>{if(key.startsWith('tempo-pending-prefix-transition'))throw new DOMException('Storage unavailable','SecurityError');return null;});
 libraryFetch();library();const rename=await screen.findByRole('menuitem',{name:'Rename',hidden:true});expect((rename as HTMLButtonElement).disabled).toBe(true);
 expect((screen.getByRole('menuitem',{name:'Delete',hidden:true}) as HTMLButtonElement).disabled).toBe(true);
});
it('audit187_resume_from_parent_repertoire_notice_reads_original_status',async()=>{
 const operation=await reserve('transport_unknown');const fetcher=vi.fn<typeof fetch>(async url=>String(url).includes('/operations/')?receipt(operation.operationId):Response.json({repertoires:[{id:'rep',name:'Audit repertoire',source_name:'audit.pgn',line_count:2,card_count:2,due_count:0}]}));vi.stubGlobal('fetch',fetcher);library();
 fireEvent.click(await screen.findByRole('button',{name:'Resume prefix transition'}));await screen.findByRole('heading',{name:'Prefix transition published'});
 const operations=fetcher.mock.calls.filter(([url])=>String(url).includes('/operations/'));expect(operations).toHaveLength(1);expect(operations[0][1]?.method).toBeUndefined();
});
it('audit187_journal_deletion_storage_event_keeps_notice_and_edit_guard',async()=>{
 await reserve('blocked');libraryFetch();library();await screen.findByRole('button',{name:'Resume prefix transition'});const rename=await screen.findByRole('menuitem',{name:'Rename',hidden:true});expect((rename as HTMLButtonElement).disabled).toBe(true);
 localStorage.removeItem(key);fireEvent(window,new StorageEvent('storage',{key,newValue:null}));await waitFor(()=>expect(readPrefixTransition('rep')?.journalMissing).toBe(true));expect(screen.queryByRole('button',{name:'Resume prefix transition'})).not.toBeNull();expect((rename as HTMLButtonElement).disabled).toBe(true);
});
// Normative assertions remain failing. Backend protection is investigated separately.
it('audit187_DEFECT_D_lost_journal_after_admission_must_keep_recovery_reachable_after_reload',async()=>{
 await reserve('blocked');libraryFetch();const first=library();await screen.findByRole('button',{name:'Resume prefix transition'});localStorage.removeItem(key);first.unmount();library();await screen.findByRole('heading',{name:'Audit repertoire'});
 expect(screen.queryByRole('button',{name:'Resume prefix transition'})).not.toBeNull();
});
it('audit187_DEFECT_D_corrupt_scope_summary_must_keep_target_structural_edits_paused',async()=>{
 const operation=await reserve();localStorage.setItem(key,JSON.stringify({...operation,summary:{...summary,affectedRepertoireIds:[]}}));libraryFetch();library();const rename=await screen.findByRole('menuitem',{name:'Rename',hidden:true});expect((rename as HTMLButtonElement).disabled).toBe(true);
});

it('audit187_same_tab_journal_deletion_is_observed_without_storage_event',async()=>{
 await reserve('blocked');libraryFetch();library();await screen.findByRole('button',{name:'Resume prefix transition'});
 localStorage.removeItem(key);
 await screen.findByText(/browser recovery record is missing/i);
 expect((screen.getByRole('menuitem',{name:'Rename',hidden:true}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'Resume prefix transition'}));
 await screen.findByRole('button',{name:'Restore saved recovery record'});
});

beforeEach(() => { installPrefixTransitionTestLocks(); });

it('audit187_unreadable_journal_remount_keeps_original_status_controls_and_verified_result',async()=>{
 const original=await reserve('blocked');localStorage.setItem(key,'broken');
 const fetcher=vi.fn<typeof fetch>(async()=>receipt(original.operationId));vi.stubGlobal('fetch',fetcher);
 panel();const check=await screen.findByRole('button',{name:'Check status'});
 expect((check as HTMLButtonElement).disabled).toBe(false);expect(fetcher).not.toHaveBeenCalled();
 fireEvent.click(check);await screen.findByRole('heading',{name:'Prefix transition published'});
 expect(localStorage.getItem(key)).toBe('broken');expect(fetcher).toHaveBeenCalledOnce();expect(fetcher.mock.calls[0][1]?.method).not.toBe('POST');
});

it.each(['missing','locks','unreadable'] as const)('audit187_%s_journal_mutation_unavailable_disables_retry_but_keeps_check_status',async failure=>{
 const original=await reserve('blocked');readPrefixTransition('rep');
 if(failure==='missing')localStorage.removeItem(key);
 if(failure==='unreadable')localStorage.setItem(key,'broken');
 if(failure==='locks')vi.stubGlobal('navigator',new Proxy(navigator,{get(target,property){return property==='locks'?undefined:Reflect.get(target,property,target);}}));
 const fetcher=vi.fn<typeof fetch>(async()=>receipt(original.operationId,'blocked'));vi.stubGlobal('fetch',fetcher);panel();
 const check=await screen.findByRole('button',{name:'Check status'});await waitFor(()=>expect((check as HTMLButtonElement).disabled).toBe(false));
 expect((screen.getByRole('button',{name:'Retry publication recovery'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(check);await waitFor(()=>expect((check as HTMLButtonElement).disabled).toBe(false));
 expect(fetcher.mock.calls.length).toBeGreaterThan(0);expect(fetcher.mock.calls.every(([,options])=>options?.method!=='POST')).toBe(true);
});
