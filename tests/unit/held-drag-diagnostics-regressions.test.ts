import { beforeEach, afterEach, expect, it, vi } from "vitest";
import type { Api } from "@lichess-org/chessground/api";
import {
  installTempoDragCapture, tempoDragDiagnostics, resetTempoDragDiagnostics,
  disableTempoDragDiagnostics, recordTempoDragPhase,
  measureTempoDragPhase, trackTempoDragPromise,
} from "../../app/lib/performance";

const fixtureCaptures: Array<{ dispose: () => void }> = [];

function fixture() {
  const surface = document.createElement("div");
  const piece = document.createElement("piece");
  piece.style.transform = "translate(225px, 325px)";
  surface.appendChild(piece);
  document.body.appendChild(surface);
  const state = {
    draggable: { current: undefined as undefined | {
      started: boolean; orig: string; origPos: [number, number]; pos: [number, number]; element: HTMLElement;
    } },
    dom: { bounds: vi.fn(() => ({ left: 10, top: 20, width: 400, height: 400 })) },
    orientation: "white", animation: { enabled: true, duration: 180 },
  };
  const capture = installTempoDragCapture(surface, { state } as unknown as Api);
  fixtureCaptures.push(capture);
  function start() {
    const grab: [number, number] = state.orientation === "black" ? [190, 100] : [240, 350];
    state.draggable.current = { started: true, orig: "e2", origPos: grab, pos: [260, 370], element: piece };
    piece.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, buttons: 1, clientX: grab[0], clientY: grab[1] }));
    vi.advanceTimersByTime(20);
  }
  function end() {
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    state.draggable.current = undefined;
    vi.advanceTimersByTime(20);
  }
  return { surface, piece, state, capture, start, end };
}

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState({}, "", "/?tempoPerformance=drag");
  resetTempoDragDiagnostics();
});
afterEach(() => {
  fixtureCaptures.splice(0).forEach(capture => capture.dispose());
  document.body.replaceChildren();
  window.history.replaceState({}, "", "/");
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("held_drag_phases_preserve_operations_started_before_the_first_hold", () => {
  const originalStart = performance.now();
  const operationId = recordTempoDragPhase("engine-work", "start");
  vi.advanceTimersByTime(100);
  const active = fixture(); active.start();
  recordTempoDragPhase("engine-work", "end", operationId);
  const session = tempoDragDiagnostics().sessions[0];
  expect(session.phases).toEqual([
    { operationId, operation: "engine-work", edge: "start", atMs: originalStart },
    { operationId, operation: "engine-work", edge: "end", atMs: performance.now() },
  ]);
  expect(session.phases[0].atMs).toBeLessThan(session.startedAtMs);
});

it("held_drag_phases_correlate_successive_holds_and_survive_session_eviction", () => {
  const active = fixture(); active.start();
  const operationId = recordTempoDragPhase("discovery-preview", "start");
  const originalStart = tempoDragDiagnostics().sessions[0].phases[0];
  active.end(); active.start();
  recordTempoDragPhase("discovery-preview", "end", operationId);
  for (const session of tempoDragDiagnostics().sessions)
    expect(session.phases).toEqual([originalStart, expect.objectContaining({ operationId, edge: "end" })]);
  const spanningId = recordTempoDragPhase("engine-work", "start");
  const spanningStart = tempoDragDiagnostics().sessions.at(-1)!.phases.at(-1)!;
  for (let hold = 0; hold < 12; hold++) { active.end(); active.start(); }
  recordTempoDragPhase("engine-work", "end", spanningId);
  const retained = tempoDragDiagnostics().sessions;
  expect(retained).toHaveLength(10);
  for (const session of retained)
    expect(session.phases.filter(phase => phase.operationId === spanningId)).toEqual([
      spanningStart, expect.objectContaining({ operationId: spanningId, edge: "end" }),
    ]);
});

it("held_drag_phase_completion_is_idempotent_and_preserves_errors_and_post_drop_work", async () => {
  const active = fixture(); active.start(); active.end();
  for (const operation of ["drop-handling", "opponent-reply", "review-persistence", "next-card-readiness"] as const) {
    const finish = measureTempoDragPhase(operation); finish(); finish(true);
  }
  const failure = new Error("synthetic engine error");
  await expect(trackTempoDragPromise("engine-work", Promise.reject(failure))).rejects.toBe(failure);
  const phases = tempoDragDiagnostics().sessions[0].phases;
  expect(phases).toHaveLength(10);
  expect(phases.at(-1)).toMatchObject({ operation: "engine-work", edge: "error" });
  for (const start of phases.filter(phase => phase.edge === "start"))
    expect(phases.filter(phase => phase.operationId === start.operationId)).toHaveLength(2);
  const operationId = recordTempoDragPhase("engine-work", "start");
  recordTempoDragPhase("discovery-preview", "end", operationId);
  expect(tempoDragDiagnostics().sessions[0].phases.at(-1)?.edge).toBe("start");
  recordTempoDragPhase("engine-work", "end", operationId);
  recordTempoDragPhase("engine-work", "end", operationId);
  expect(tempoDragDiagnostics().sessions[0].phases.filter(phase => phase.operationId === operationId)).toHaveLength(2);
});

it("held_drag_phase_reset_and_disable_invalidate_late_callbacks", () => {
  for (const invalidate of [resetTempoDragDiagnostics, disableTempoDragDiagnostics]) {
    const active = fixture(); active.start();
    const lateCompletion = measureTempoDragPhase("engine-work");
    const oldId = tempoDragDiagnostics().sessions.at(-1)!.phases[0].operationId;
    invalidate(); resetTempoDragDiagnostics();
    active.start();
    const newCompletion = measureTempoDragPhase("engine-work");
    lateCompletion(); recordTempoDragPhase("engine-work", "error", oldId);
    newCompletion();
    const phases = tempoDragDiagnostics().sessions[0].phases;
    expect(phases).toHaveLength(2);
    expect(phases.every(phase => phase.operationId !== oldId)).toBe(true);
    active.capture.dispose(); resetTempoDragDiagnostics();
  }
});

it("held_drag_active_operations_are_bounded_and_snapshot_tracking_is_isolated", () => {
  const operationIds = Array.from({ length: 128 }, () => recordTempoDragPhase("engine-work", "start"));
  expect(recordTempoDragPhase("engine-work", "start")).toBe(0);
  const snapshot = tempoDragDiagnostics();
  expect(snapshot).toMatchObject({ schemaVersion: 2, phaseTracking: { activeOperations: 128, truncated: true } });
  snapshot.phaseTracking.activeOperations = 0;
  const active = fixture(); active.start();
  expect(tempoDragDiagnostics().sessions[0].phases).toHaveLength(128);
  operationIds.forEach(operationId => recordTempoDragPhase("engine-work", "end", operationId));
  expect(tempoDragDiagnostics().sessions[0].truncated.phases).toBe(true);
  expect(tempoDragDiagnostics()).toMatchObject({ phaseTracking: { activeOperations: 0, truncated: true } });
  resetTempoDragDiagnostics();
  expect(tempoDragDiagnostics()).toMatchObject({ phaseTracking: { activeOperations: 0, truncated: false } });
});

it("held_drag_capture_is_bounded_and_disabled_by_default", () => {
  window.history.replaceState({}, "", "/");
  const disabled = fixture();
  disabled.start();
  expect(tempoDragDiagnostics().sessions).toEqual([]);
  expect(disabled.state.dom.bounds).not.toHaveBeenCalled();
  disabled.capture.dispose();
  window.history.replaceState({}, "", "/?tempoPerformance=drag");
  const active = fixture();
  for (let session = 0; session < 12; session++) {
    active.start();
    for (let sample = 0; sample < 600; sample++)
      document.dispatchEvent(new MouseEvent("mousemove", { clientX: sample, clientY: sample, buttons: 1 }));
    vi.advanceTimersByTime(10_000);
    active.end();
  }
  const snapshot = tempoDragDiagnostics();
  expect(snapshot.sessions).toHaveLength(10);
  expect(snapshot.sessions.every(session => session.events.length <= 512 && session.frames.length <= 512)).toBe(true);
  expect(snapshot.sessions.every(session => session.truncated.events && session.truncated.frames)).toBe(true);
  active.start();
  vi.advanceTimersByTime(30_100);
  expect(tempoDragDiagnostics().sessions.at(-1)?.endReason).toBe("capture-limit");
  expect(active.state.draggable.current).toBeDefined();
  active.capture.dispose();
});

it("held_drag_capture_cleans_up_on_visibility_cancel_capture_loss_and_unmount", () => {
  for (const [event, reason] of [["pointercancel", "pointer-cancel"], ["lostpointercapture", "lost-capture"]]) {
    const active = fixture(); active.start();
    document.dispatchEvent(new Event(event));
    expect(tempoDragDiagnostics().sessions.at(-1)?.endReason).toBe(reason);
    active.capture.dispose();
  }
  const hidden = fixture(); hidden.start();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(tempoDragDiagnostics().sessions.at(-1)?.endReason).toBe("hidden-tab");
  hidden.capture.dispose(); vi.restoreAllMocks();
  const disabled = fixture(); disabled.start(); disableTempoDragDiagnostics();
  expect(tempoDragDiagnostics().sessions.at(-1)?.endReason).toBe("disabled");
  disabled.capture.dispose(); resetTempoDragDiagnostics();
  for (let mount = 0; mount < 3; mount++) {
    const active = fixture(); active.start(); active.capture.dispose();
    expect(tempoDragDiagnostics().sessions.at(-1)?.endReason).toBe("unmount");
    const samples = tempoDragDiagnostics().sessions.at(-1)!.frames.length;
    vi.advanceTimersByTime(100);
    expect(tempoDragDiagnostics().sessions.at(-1)!.frames).toHaveLength(samples);
    document.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  }
});

it("held_drag_metrics_account_for_grab_offset_orientation_and_coalescing", () => {
  vi.stubGlobal("devicePixelRatio", 2);
  const active = fixture(); active.state.orientation = "black"; active.start();
  const move = new MouseEvent("pointermove", { clientX: 260, clientY: 370, buttons: 1 });
  Object.defineProperty(move, "getCoalescedEvents", { value: () => [
    new MouseEvent("pointermove", { clientX: 250, clientY: 360 }),
    new MouseEvent("pointermove", { clientX: 260, clientY: 370 }),
  ] });
  document.dispatchEvent(move); vi.advanceTimersByTime(20);
  recordTempoDragPhase("opponent-reply", "start"); active.end();
  const session = tempoDragDiagnostics().sessions.at(-1)!;
  expect(session.orientation).toBe("black");
  expect(session.viewport.devicePixelRatio).toBe(2);
  expect(session.initialGrabOffsetCssPx).toEqual([5, 5]);
  expect(session.events.find(event => event.type === "pointermove")?.coalescedCount).toBe(2);
  expect(session.frames.at(-1)?.displacementCssPx).toBe(0);
  expect(session.frames.at(-1)?.eventAgeMs).toBeGreaterThanOrEqual(0);
  expect(session.frames.at(-1)?.captureCostMs).toBeGreaterThanOrEqual(0);
  expect(session.events.find(event => event.type === "pointermove")?.captureCostMs).toBeGreaterThanOrEqual(0);
  expect(session.phases.some(phase => phase.operation === "opponent-reply")).toBe(true);
  expect(active.state.dom.bounds).toHaveBeenCalledTimes(1);
  active.capture.dispose();
});

it("held_drag_detector_reports_mid_drag_board_cancellation", () => {
  const active = fixture(); active.start();
  active.capture.boardEvent("cancel-move", ["orientation"]);
  active.state.draggable.current = undefined;
  vi.advanceTimersByTime(20);
  const session = tempoDragDiagnostics().sessions.at(-1)!;
  expect(session.endReason).toBe("board-interruption");
  expect(session.boardEvents).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "cancel-move", changed: ["orientation"] })]));
  expect(session.events.some(event => event.type === "mouseup")).toBe(false);
  active.capture.dispose();
});

it("held_drag_reset_reuses_capture_without_pointer_storage_network_or_layout_work", () => {
  const active = fixture(); active.start();
  const store = vi.spyOn(Storage.prototype, "setItem");
  const layout = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect");
  const network = vi.spyOn(globalThis, "fetch");
  for (let move = 0; move < 50; move++)
    document.dispatchEvent(new MouseEvent("mousemove", { clientX: 260, clientY: 370, buttons: 1 }));
  vi.advanceTimersByTime(100);
  expect(store).not.toHaveBeenCalled(); expect(layout).not.toHaveBeenCalled(); expect(network).not.toHaveBeenCalled();
  active.end(); resetTempoDragDiagnostics(); active.start();
  expect(tempoDragDiagnostics().sessions).toHaveLength(1);
  const snapshot = tempoDragDiagnostics(); snapshot.sessions[0].events.length = 0;
  expect(tempoDragDiagnostics().sessions[0].events.length).toBeGreaterThan(0);
  active.capture.dispose();
});

it("held_drag_frame_cadence_uses_callback_receipt_not_nominal_rAF_timestamp", () => {
  const pendingFrames: FrameRequestCallback[] = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation(callback => {
    pendingFrames.push(callback); return pendingFrames.length;
  });
  const active = fixture(); active.start();
  pendingFrames.shift()!(16);
  vi.advanceTimersByTime(80);
  pendingFrames.shift()!(32);
  const frames = tempoDragDiagnostics().sessions.at(-1)!.frames;
  expect(frames.at(-1)?.rAFGapMs).toBe(16);
  expect(frames.at(-1)?.gapMs).toBe(80);
  active.capture.dispose();
});

it("held_drag_long_tasks_and_operation_correlations_are_bounded", () => {
  let collect: PerformanceObserverCallback | undefined;
  const disconnect = vi.fn();
  vi.stubGlobal("PerformanceObserver", class {
    static supportedEntryTypes = ["longtask"];
    constructor(callback: PerformanceObserverCallback) { collect = callback; }
    observe() {}
    takeRecords() { return []; }
    disconnect = disconnect;
  });
  const active = fixture(); active.start();
  collect!({ getEntries: () => Array.from({ length: 100 }, () => ({ startTime: performance.now(), duration: 80 })) } as unknown as PerformanceObserverEntryList, {} as PerformanceObserver);
  for (let operation = 0; operation < 200; operation++) {
    recordTempoDragPhase("engine-work", "start");
    active.capture.boardEvent("annotations");
  }
  const captured = tempoDragDiagnostics().sessions.at(-1)!;
  expect(captured.longTasks).toHaveLength(64);
  expect(captured.phases).toHaveLength(128);
  expect(captured.boardEvents).toHaveLength(128);
  expect(captured.truncated).toMatchObject({ longTasks: true, phases: true, boardEvents: true });
  active.capture.dispose(); expect(disconnect).toHaveBeenCalled();
});

it("held_drag_snapshot_metadata_cannot_change_live_capture_limits", () => {
  const active = fixture(); active.start();
  const snapshot = tempoDragDiagnostics();
  try {
    Reflect.set(snapshot.limits, "events", 1);
    for (let move = 0; move < 20; move++)
      document.dispatchEvent(new MouseEvent("mousemove", { clientX: 260, clientY: 370, buttons: 1 }));
    expect(tempoDragDiagnostics().sessions.at(-1)!.events.length).toBeGreaterThan(1);
  } finally {
    Reflect.set(snapshot.limits, "events", 512);
    active.capture.dispose();
  }
});
