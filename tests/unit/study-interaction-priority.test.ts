import { afterEach, beforeEach, expect, it, vi } from 'vitest';

let stop: (() => void) | undefined;
beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); });
afterEach(async () => { stop?.(); stop = undefined; await vi.advanceTimersByTimeAsync(3_001); vi.useRealTimers(); vi.unstubAllGlobals(); });

it('study priority keeps a stationary held pointer protected, renews its session and resumes after three quiet seconds', async () => {
  const priority = await import('../../app/lib/study-priority');
  const send = vi.fn(async (session: string) => { void session; });
  priority.setStudyPriorityScope(true);
  stop = priority.installStudyInteractionPriority(send);
  const down = new Event('pointerdown'); Object.defineProperty(down, 'pointerId', { value: 4 });
  window.dispatchEvent(down);
  expect(priority.optionalStudyWorkPaused()).toBe(true);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(priority.studyInteractionActive()).toBe(true);
  expect(send.mock.calls.length).toBeGreaterThan(10);
  expect(new Set(send.mock.calls.map(call => call[0])).size).toBe(1);
  const up = new Event('pointerup'); Object.defineProperty(up, 'pointerId', { value: 4 });
  window.dispatchEvent(up);
  await vi.advanceTimersByTimeAsync(2_999);
  expect(priority.studyInteractionActive()).toBe(true);
  await vi.advanceTimersByTimeAsync(1);
  expect(priority.studyInteractionActive()).toBe(false);
});

it('overlapping study operations do not release each other and an idle wait is abortable', async () => {
  const priority = await import('../../app/lib/study-priority');
  const first = priority.holdStudyPriority(), second = priority.holdStudyPriority();
  first(); await vi.advanceTimersByTimeAsync(4_000);
  expect(priority.studyInteractionActive()).toBe(true);
  const controller = new AbortController();
  const waiting = priority.waitForStudyIdle(controller.signal);
  const rejected = expect(waiting).rejects.toMatchObject({ name: 'AbortError' });
  controller.abort(); await rejected;
  second(); await vi.advanceTimersByTimeAsync(3_000);
  expect(priority.studyInteractionActive()).toBe(false);
  priority.setStudyOnlyPreparationPaused(true);
  await vi.advanceTimersByTimeAsync(10_000);
  expect(priority.optionalStudyWorkPaused()).toBe(true);
  priority.setStudyOnlyPreparationPaused(false);
});

it('another tab suspends local preparation without renewing another session indefinitely', async () => {
  class Channel {
    static instance: Channel;
    onmessage: ((event: { data: unknown }) => void) | null = null;
    postMessage = vi.fn(); close = vi.fn();
    constructor() { Channel.instance = this; }
  }
  vi.stubGlobal('BroadcastChannel', Channel);
  const priority = await import('../../app/lib/study-priority');
  stop = priority.installStudyInteractionPriority();
  Channel.instance.onmessage?.({ data: { version: 1, session: 'peer', until: Date.now() + 3_000 } });
  expect(priority.optionalStudyWorkPaused()).toBe(true);
  await vi.advanceTimersByTimeAsync(3_000);
  expect(priority.optionalStudyWorkPaused()).toBe(false);
  expect(Channel.instance.postMessage).not.toHaveBeenCalled();
});

it('study_priority_quiet_window_and_held_renewals_use_elapsed_time_when_wall_clock_changes',async()=>{
  const priority=await import('../../app/lib/study-priority');
  priority.setStudyPriorityScope(true);
  const send=vi.fn(async()=>undefined);stop=priority.installStudyInteractionPriority(send);
  const release=priority.holdStudyPriority();
  vi.setSystemTime(Date.now()-3600000);
  await vi.advanceTimersByTimeAsync(1000);
  expect(send.mock.calls.length).toBeGreaterThan(1);
  release();await vi.advanceTimersByTimeAsync(3000);
  expect(priority.studyInteractionActive()).toBe(false);
});

it('study_priority_touch_only_hold_protects_stationary_contact_until_end_and_quiet_window',async()=>{
  const priority=await import('../../app/lib/study-priority');priority.setStudyPriorityScope(true);
  stop=priority.installStudyInteractionPriority();
  const touch=(kind:string)=>{const event=new Event(kind);Object.defineProperty(event,'changedTouches',{value:[{identifier:7}]});window.dispatchEvent(event);};
  touch('touchstart');await vi.advanceTimersByTimeAsync(5000);expect(priority.studyInteractionActive()).toBe(true);
  touch('touchend');await vi.advanceTimersByTimeAsync(3000);expect(priority.studyInteractionActive()).toBe(false);
});
