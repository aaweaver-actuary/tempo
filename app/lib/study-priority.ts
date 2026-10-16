import { recordTempoDuration } from "./performance";
/** Browser-local study priority. The input handler only updates small in-memory state. */
export const STUDY_QUIET_MS = 3_000;
const listeners = new Set<() => void>();
const holds = new Set<symbol>();
let activeUntil = 0;
let quietTimer: ReturnType<typeof setTimeout> | undefined;
let studyScope = false;
let studyOnly = false;
const remoteActivity = new Map<string, number>();

function localActivityActive() { return holds.size > 0 || performance.now() < activeUntil; }
export function studyInteractionActive() {
  const now = performance.now();
  for (const [session, until] of remoteActivity) if (until <= now) remoteActivity.delete(session);
  return localActivityActive() || remoteActivity.size > 0;
}
export function optionalStudyWorkPaused() { return studyOnly || studyInteractionActive(); }
export function subscribeStudyPriority(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function publish() { for (const listener of listeners) listener(); }
function armQuietTimer() {
  clearTimeout(quietTimer);
  quietTimer = undefined;
  const until = Math.max(activeUntil, ...remoteActivity.values());
  if (!holds.size && until > performance.now()) quietTimer = setTimeout(() => {
    quietTimer = undefined;
    armQuietTimer();
    publish();
  }, Math.max(1, Math.ceil(until - performance.now())));
}
export function markStudyInteraction() {
  const started = performance.now();
  activeUntil = performance.now() + STUDY_QUIET_MS;
  armQuietTimer();
  publish();
  recordTempoDuration("study-priority-yield", performance.now() - started);
}
export function setStudyPriorityScope(enabled: boolean) { studyScope = enabled; }
export function setStudyOnlyPreparationPaused(paused: boolean) {
  if (studyOnly !== paused) { studyOnly = paused; publish(); }
}
export function holdStudyPriority() {
  const token = Symbol('study-operation');
  holds.add(token);
  markStudyInteraction();
  return () => {
    if (!holds.delete(token)) return;
    markStudyInteraction();
  };
}
export function holdActiveStudyRequest() { return studyScope ? holdStudyPriority() : () => {}; }

export function waitForStudyIdle(signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(signal.reason ?? new DOMException('Cancelled', 'AbortError'));
  if (!optionalStudyWorkPaused()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const finish = (error?: unknown) => {
      unsubscribe(); signal?.removeEventListener('abort', abort);
      if (error) reject(error); else resolve();
    };
    const abort = () => finish(signal?.reason ?? new DOMException('Cancelled', 'AbortError'));
    const unsubscribe = subscribeStudyPriority(() => { if (!optionalStudyWorkPaused()) finish(); });
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
  });
}

/** Own listeners/pulses for one tab; other tabs retain independent expiring leases. */
export function installStudyInteractionPriority(sendActivity?: (session: string, remainingMs: number) => Promise<unknown>) {
  const session = crypto.randomUUID();
  const channel = typeof BroadcastChannel === 'undefined' ? undefined : new BroadcastChannel('tempo-study-priority-v1');
  if (channel) channel.onmessage = ({ data }) => {
    if (data?.version !== 1 || typeof data.session !== 'string' || data.session === session ||
        typeof data.until !== 'number' || !Number.isFinite(data.until)) return;
    remoteActivity.set(data.session, performance.now() + Math.max(0, Math.min(STUDY_QUIET_MS, data.until - Date.now())));
    armQuietTimer(); publish();
  };
  const pointers = new Map<number, () => void>();
  const touches = new Map<number, () => void>();
  let sending = false;
  let activityUnavailable = false;
  let stopped = false;
  let lastSent = -Infinity;
  let pulseTimer: ReturnType<typeof setTimeout> | undefined;
  const pulse = () => {
    if (stopped || !localActivityActive() || document.visibilityState === 'hidden') return;
    if (sending || performance.now() - lastSent < 100) {
      pulseTimer ??= setTimeout(() => { pulseTimer = undefined; pulse(); }, 100);
      return;
    }
    lastSent = performance.now();
    const remainingMs = Math.max(1, Math.ceil(holds.size ? STUDY_QUIET_MS : activeUntil - performance.now()));
    channel?.postMessage({ version: 1, session, until: Date.now() + remainingMs });
    if (sendActivity) {
      sending = true;
      void sendActivity(session, remainingMs).then(() => { activityUnavailable = false; }).catch(error => {
        if (!activityUnavailable) console.warn('Study host activity is unavailable; managed work must defer.', error);
        activityUnavailable = true;
      }).finally(() => { sending = false; });
    }
  };
  const unsubscribe = subscribeStudyPriority(pulse);
  let framePending = false;
  const recordInputFrame = () => {
    if (framePending) return;
    framePending = true;
    const startedAt = performance.now();
    requestAnimationFrame(() => {
      framePending = false;
      if (!stopped) recordTempoDuration('study-input-response', performance.now() - startedAt);
    });
  };
  const mark = () => { if (studyScope) { markStudyInteraction(); recordInputFrame(); } };
  const down = (event: PointerEvent) => {
    if (!studyScope) return;
    pointers.get(event.pointerId)?.();
    pointers.set(event.pointerId, holdStudyPriority());
    recordInputFrame();
  };
  const up = (event: PointerEvent) => { pointers.get(event.pointerId)?.(); pointers.delete(event.pointerId); };
  const move = () => { if (pointers.size) mark(); };
  const touchStart = (event: TouchEvent) => {
    if(!studyScope)return;
    for(const touch of Array.from(event.changedTouches)) if(!touches.has(touch.identifier))touches.set(touch.identifier,holdStudyPriority());
    recordInputFrame();
  };
  const touchEnd = (event: TouchEvent) => {for(const touch of Array.from(event.changedTouches)){touches.get(touch.identifier)?.();touches.delete(touch.identifier);}};
  const release = () => { for (const finish of [...pointers.values(),...touches.values()]) finish(); pointers.clear();touches.clear(); };
  const visibility = () => { if (document.visibilityState === 'hidden') release(); else pulse(); };
  window.addEventListener('pointerdown', down, true);
  window.addEventListener('pointermove', move, { capture: true, passive: true });
  window.addEventListener('pointerup', up, true);
  window.addEventListener('pointercancel', up, true);
  window.addEventListener('keydown', mark, true);
  window.addEventListener('wheel', mark, { capture: true, passive: true });
  window.addEventListener('scroll', mark, { capture: true, passive: true });
  window.addEventListener('touchstart', touchStart, { capture: true, passive: true });
  window.addEventListener('touchend',touchEnd,true);
  window.addEventListener('touchcancel',touchEnd,true);
  window.addEventListener('blur', release);
  window.addEventListener('popstate', mark);
  document.addEventListener('visibilitychange', visibility);
  // Renew held pointers and requests even when there are no new input events.
  const renewal = setInterval(pulse, 500);
  return () => {
    stopped = true; release(); unsubscribe(); channel?.close();
    clearInterval(renewal); clearTimeout(pulseTimer);
    window.removeEventListener('pointerdown', down, true);
    window.removeEventListener('pointermove', move, true);
    window.removeEventListener('pointerup', up, true);
    window.removeEventListener('pointercancel', up, true);
    window.removeEventListener('keydown', mark, true);
    window.removeEventListener('wheel', mark, true);
    window.removeEventListener('scroll',mark,true);
    window.removeEventListener('touchstart', touchStart, true);
    window.removeEventListener('touchend',touchEnd,true);
    window.removeEventListener('touchcancel',touchEnd,true);
    window.removeEventListener('blur', release);
    window.removeEventListener('popstate', mark);
    document.removeEventListener('visibilitychange', visibility);
  };
}
