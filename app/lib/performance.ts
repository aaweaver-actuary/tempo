export type TempoTiming = {
  operation: "board-ready" | "move-to-paint" | "view-switch" |
    "study-worker-queue" | "study-worker-compute" | "study-worker-roundtrip" |
    "study-match-coalesced-wait" | "api-response" | "workspace-data-ready";
  duration: number;
  recordedAt: number;
  resource?: string;
};
const timings: TempoTiming[] = [];

export function recordTempoDuration(operation: TempoTiming["operation"], duration: number, resource?: string) {
  timings.push({ operation, duration, recordedAt: Date.now(), ...(resource ? { resource } : {}) });
  if (timings.length > 200) timings.shift();
}

export function measureTempoOperation(operation: TempoTiming["operation"]) {
  const startedAt = performance.now();
  return () => {
    const endedAt = performance.now();
    recordTempoDuration(operation, endedAt - startedAt);
    if (typeof performance.measure === "function") {
      performance.clearMeasures?.(`tempo:${operation}`);
      performance.measure(`tempo:${operation}`, {
        start: startedAt,
        end: endedAt,
      });
    }
  };
}

export function tempoPerformanceTimings(): readonly TempoTiming[] {
  return timings.slice();
}

// Held-drag diagnostics share the existing in-memory performance facility.
// These are event/DOM-transform scheduling proxies, never proof of presentation.
export type TempoDragPhase = "drop-handling" | "opponent-reply" |
  "review-persistence" | "next-card-readiness" | "discovery-preview" | "engine-work";
export type TempoDragEndReason = "drop" | "board-interruption" | "pointer-cancel" |
  "lost-capture" | "geometry-change" | "hidden-tab" | "unmount" | "disabled" | "reset" | "capture-limit";
export type TempoDragEvent = {
  type: string; eventAtMs: number; receivedAtMs: number; xCssPx: number; yCssPx: number;
  coalescedCount: number; captureCostMs?: number;
};
export type TempoDragFrame = {
  atMs: number; callbackAtMs: number; gapMs: number | null; rAFGapMs: number | null; eventAgeMs: number | null;
  displacementCssPx: number | null; xCssPx: number | null; yCssPx: number | null; captureCostMs?: number;
};
type PhaseSample = { operationId: number; operation: TempoDragPhase; edge: "start" | "end" | "error"; atMs: number };
export type TempoDragSession = {
  id: number; startedAtMs: number; endedAtMs: number | null; endReason: TempoDragEndReason | null;
  orientation: string; viewport: { width: number; height: number; devicePixelRatio: number };
  boardGeometry: { left: number; top: number; width: number; height: number };
  animation: { enabled: boolean; durationMs: number }; initialGrabOffsetCssPx: [number, number];
  events: TempoDragEvent[]; frames: TempoDragFrame[];
  boardEvents: Array<{ kind: "cancel-move" | "set" | "annotations" | "redraw"; changed: string[]; atMs: number }>;
  longTasks: Array<{ atMs: number; durationMs: number }>; phases: PhaseSample[];
  longTasksSupported: boolean;
  truncated: { events: boolean; frames: boolean; boardEvents: boolean; longTasks: boolean; phases: boolean };
};

const dragSessions: TempoDragSession[] = [];
const dragCleanups = new Set<(reason: TempoDragEndReason) => void>();
let dragDisabled = false;
let nextDragId = 1;
let nextPhaseId = 1;
const dragLimits = { sessions: 10, events: 512, frames: 512, longTasks: 64, boardEvents: 128, phases: 128, durationMs: 30_000 } as const;

function dragCaptureEnabled() {
  return !dragDisabled && typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("tempoPerformance") === "drag";
}

export function tempoDragDiagnostics() {
  return {
    schemaVersion: 1, enabled: dragCaptureEnabled(), limits: { ...dragLimits },
    units: { timestamps: "performance time origin milliseconds", displacement: "CSS pixels" },
    measurement: "event receipt and DOM transform at requestAnimationFrame; not physical presentation or INP",
    sessions: structuredClone(dragSessions),
  };
}

export function disableTempoDragDiagnostics() {
  dragDisabled = true;
  for (const cleanup of [...dragCleanups]) cleanup("disabled");
}

export function resetTempoDragDiagnostics() {
  for (const cleanup of [...dragCleanups]) cleanup("reset");
  dragSessions.length = 0;
  // Reset may clear a disabled capture, but cannot enable it without the URL opt-in.
  dragDisabled = false;
}

export function recordTempoDragPhase(operation: TempoDragPhase, edge: PhaseSample["edge"], operationId?: number) {
  if (!dragCaptureEnabled()) return 0;
  const id = operationId ?? nextPhaseId++;
  const session = dragSessions.at(-1);
  if (session) {
    if (session.phases.length < dragLimits.phases)
      session.phases.push({ operationId: id, operation, edge, atMs: performance.now() });
    else session.truncated.phases = true;
  }
  return id;
}

export function measureTempoDragPhase(operation: TempoDragPhase) {
  const operationId = recordTempoDragPhase(operation, "start");
  let finished = false;
  return (failed = false) => {
    if (finished || !operationId) return;
    finished = true;
    recordTempoDragPhase(operation, failed ? "error" : "end", operationId);
  };
}

function translatedPiecePosition(element: HTMLElement) {
  const translate = element.style.transform.match(/^translate\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px\s*\)$/);
  return translate ? [Number(translate[1]), Number(translate[2])] : null;
}

export function installTempoDragCapture(surface: HTMLElement, api: import("@lichess-org/chessground/api").Api) {
  let session: TempoDragSession | undefined;
  let pendingEvents: TempoDragEvent[] = [];
  let frame = 0;
  let limitTimer: ReturnType<typeof setTimeout> | undefined;
  let released = false;
  let pending = false;
  let disposed = false;
  let longTaskObserver: PerformanceObserver | undefined;
  let latestEvent: TempoDragEvent | undefined;
  let pointerEventsSeen = false;
  let pendingEventsTruncated = false;
  const removals: Array<() => void> = [];

  function finish(reason: TempoDragEndReason) {
    if (session && !session.endReason) {
      collectLongTasks(longTaskObserver?.takeRecords() ?? []);
      session.endReason = reason;
      session.endedAtMs = performance.now();
    }
    longTaskObserver?.disconnect(); longTaskObserver = undefined;
    if (limitTimer !== undefined) clearTimeout(limitTimer);
    limitTimer = undefined;
    cancelAnimationFrame(frame); frame = 0;
    pending = false; pendingEvents = []; latestEvent = undefined;
  }
  function collectLongTasks(entries: PerformanceEntry[]) {
    if (!session) return;
    for (const entry of entries) {
      if (entry.startTime + entry.duration < session.startedAtMs) continue;
      if (session.longTasks.length < dragLimits.longTasks)
        session.longTasks.push({ atMs: entry.startTime, durationMs: entry.duration });
      else session.truncated.longTasks = true;
    }
  }
  function observeFrame(atMs: number) {
    const captureStartedAtMs = performance.now();
    frame = 0;
    if (!pending || disposed) return;
    const drag = api.state.draggable.current;
    if (!drag) { finish(released ? "drop" : "board-interruption"); return; }
    if (!drag.started) { frame = requestAnimationFrame(observeFrame); return; }
    const element = typeof drag.element === "function" ? undefined : drag.element;
    if (!session || session.endReason) {
      const bounds = api.state.dom.bounds();
      const originalFile = drag.orig?.charCodeAt(0) - 97;
      const originalRank = Number(drag.orig?.[1]) - 1;
      const white = api.state.orientation === "white";
      const translated = element && translatedPiecePosition(element);
      const centerX = Number.isFinite(originalFile) ? bounds.left + ((white ? originalFile : 7 - originalFile) + 0.5) * bounds.width / 8
        : bounds.left + (translated?.[0] ?? 0) + bounds.width / 16;
      const centerY = Number.isFinite(originalRank) ? bounds.top + ((white ? 7 - originalRank : originalRank) + 0.5) * bounds.height / 8
        : bounds.top + (translated?.[1] ?? 0) + bounds.height / 16;
      session = {
        id: nextDragId++, startedAtMs: performance.now(), endedAtMs: null, endReason: null,
        orientation: api.state.orientation,
        viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio },
        boardGeometry: { left: bounds.left, top: bounds.top, width: bounds.width, height: bounds.height },
        animation: { enabled: api.state.animation.enabled, durationMs: api.state.animation.duration },
        initialGrabOffsetCssPx: [drag.origPos[0] - centerX, drag.origPos[1] - centerY],
        events: pendingEvents, frames: [], boardEvents: [], longTasks: [], phases: [],
        longTasksSupported: typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes.includes("longtask"),
        truncated: { events: pendingEventsTruncated, frames: false, boardEvents: false, longTasks: false, phases: false },
      };
      pendingEvents = [];
      dragSessions.push(session);
      if (dragSessions.length > dragLimits.sessions) dragSessions.shift();
      if (session.longTasksSupported) {
        longTaskObserver = new PerformanceObserver(list => collectLongTasks(list.getEntries()));
        longTaskObserver.observe({ type: "longtask" });
      }
      if (limitTimer !== undefined) clearTimeout(limitTimer);
      limitTimer = setTimeout(() => finish("capture-limit"), dragLimits.durationMs);
    }
    const position = element && translatedPiecePosition(element);
    const geometry = session.boardGeometry;
    // Chessground centers the piece under the pointer once dragging starts;
    // its initial grab offset must not be misclassified as sustained lag.
    const xCssPx = position ? geometry.left + position[0] + geometry.width / 16 : null;
    const yCssPx = position ? geometry.top + position[1] + geometry.height / 16 : null;
    const previous = session.frames.at(-1);
    if (session.frames.length < dragLimits.frames) {
      session.frames.push({ atMs, callbackAtMs: captureStartedAtMs,
        gapMs: previous ? captureStartedAtMs - previous.callbackAtMs : null,
        rAFGapMs: previous ? atMs - previous.atMs : null,
        eventAgeMs: latestEvent ? Math.max(0, captureStartedAtMs - latestEvent.eventAtMs) : null,
        displacementCssPx: latestEvent && xCssPx !== null && yCssPx !== null
          ? Math.hypot(xCssPx - latestEvent.xCssPx, yCssPx - latestEvent.yCssPx) : null,
        xCssPx, yCssPx });
    } else session.truncated.frames = true;
    frame = requestAnimationFrame(observeFrame);
    const observed = session.frames.at(-1);
    if (observed?.atMs === atMs) observed.captureCostMs = performance.now() - captureStartedAtMs;
  }
  function recordInput(event: Event) {
    if (!pending) return;
    const receivedAtMs = performance.now();
    const pointer = event as PointerEvent;
    if (event.type.startsWith("pointer")) pointerEventsSeen = true;
    if (pointerEventsSeen && event.type.startsWith("mouse")) return;
    const coalesced = typeof pointer.getCoalescedEvents === "function" ? pointer.getCoalescedEvents() : [];
    const last = coalesced.at(-1) ?? pointer;
    latestEvent = {
      type: event.type, eventAtMs: last.timeStamp > 1e12 ? last.timeStamp - performance.timeOrigin : last.timeStamp,
      receivedAtMs, xCssPx: last.clientX, yCssPx: last.clientY, coalescedCount: coalesced.length,
    };
    const events = session && !session.endReason ? session.events : pendingEvents;
    if (events.length < dragLimits.events) events.push(latestEvent);
    else if (session) session.truncated.events = true;
    else pendingEventsTruncated = true;
    latestEvent.captureCostMs = performance.now() - receivedAtMs;
  }
  function start(event: Event) {
    if (pending || disposed || !dragCaptureEnabled() || document.visibilityState === "hidden") return;
    // Piece elements use pointer-events:none; Chessground receives the board
    // target. Confirm an actual drag from its state on the following frame.
    if ((event as MouseEvent).button !== 0) return;
    pending = true; released = false; session = undefined; pointerEventsSeen = false; pendingEventsTruncated = false;
    recordInput(event);
    limitTimer = setTimeout(() => finish("capture-limit"), dragLimits.durationMs);
    frame = requestAnimationFrame(observeFrame);
  }
  function release(event: Event) {
    if (!pending) return;
    recordInput(event); released = true;
    finish("drop");
  }
  function listen(target: EventTarget, type: string, handler: EventListener, capture = false) {
    target.addEventListener(type, handler, { passive: true, capture });
    removals.push(() => target.removeEventListener(type, handler, capture));
  }
  function dispose(reason: TempoDragEndReason = "unmount") {
    if (disposed) return;
    disposed = true; finish(reason);
    removals.forEach(remove => remove());
    dragCleanups.delete(cleanup);
  }
  function cleanup(reason: TempoDragEndReason) {
    // Reset keeps the installed observer usable for the next drag.
    if (reason === "reset" || reason === "disabled") finish(reason);
    else dispose(reason);
  }
  if (dragCaptureEnabled()) {
    listen(surface, "pointerdown", start); listen(surface, "mousedown", start);
    for (const type of ["pointermove", "mousemove"]) listen(document, type, recordInput);
    for (const type of ["pointerup", "mouseup"]) listen(document, type, release);
    listen(document, "pointercancel", () => finish("pointer-cancel"));
    listen(document, "lostpointercapture", () => finish("lost-capture"));
    listen(document, "visibilitychange", () => { if (document.visibilityState === "hidden") finish("hidden-tab"); });
    listen(document, "scroll", () => finish("geometry-change"), true);
    listen(window, "resize", () => finish("geometry-change"));
    dragCleanups.add(cleanup);
    Object.assign(window, { tempoPerformance: {
      snapshot: tempoDragDiagnostics, reset: resetTempoDragDiagnostics, disable: disableTempoDragDiagnostics,
    } });
  }
  return {
    dispose,
    boardEvent(kind: TempoDragSession["boardEvents"][number]["kind"], changed: string[] = []) {
      if (!pending || !session || session.endReason) return;
      if (session.boardEvents.length < dragLimits.boardEvents)
        session.boardEvents.push({ kind, changed, atMs: performance.now() });
      else session.truncated.boardEvents = true;
      // A resize/scroll can invalidate cached geometry; end this measurement
      // rather than fabricate displacement from stale bounds.
      if (kind === "redraw") finish("geometry-change");
      else if (!api.state.draggable.current) finish("board-interruption");
    },
  };
}


export function trackTempoDragPromise<T>(operation: TempoDragPhase, promise: Promise<T>): Promise<T> {
  if (!dragCaptureEnabled()) return promise;
  const finish = measureTempoDragPhase(operation);
  return promise.then(value => { finish(); return value; }, error => { finish(true); throw error; });
}
