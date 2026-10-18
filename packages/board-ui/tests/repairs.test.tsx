import { StrictMode, act } from "react";
import { createRoot } from "react-dom/client";
import { JSDOM } from "jsdom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Board } from "../src/Board.js";
import { BoardController } from "../src/controller.js";
import { createRenderer } from "../src/renderer.js";
import type { BoardProps, BoardShape } from "../src/types.js";

const base: BoardProps = { fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1", positionIdentity: "repairs",
  positionRevision: 0, orientation: "white", turn: "white", check: false, mode: "legal",
  destinations: new Map([["e2", ["e4"]]]) };
let cleanup = () => {};
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, left: 0, top: 0, right: 643, bottom: 643, width: 643, height: 643, toJSON() {},
  });
});
afterEach(() => { cleanup(); cleanup = () => {}; vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllTimers(); vi.useRealTimers(); });
function renderer(props = base) {
  vi.useFakeTimers(); vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const host = document.createElement("div"); document.body.append(host);
  const controller = new BoardController();
  const api = createRenderer(host, controller.initialConfiguration()); controller.attach(api); controller.update(props);
  const dispose = () => { controller.destroy(); host.remove(); };
  cleanup = dispose;
  return { host, controller, api, dispose };
}
const frame = () => vi.advanceTimersByTimeAsync(40);
function hold(host: HTMLElement, api: ReturnType<typeof createRenderer>, button = 0) {
  // jsdom input is untrusted. Only this trust check is bypassed; pinned input,
  // DOM rendering, callbacks and frame scheduling are exercised unchanged.
  api.state.trustAllEvents = true;
  host.querySelector("cg-board")!.dispatchEvent(new MouseEvent("mousedown", {
    clientX: 643 * 4.5 / 8, clientY: 643 * 6.5 / 8, button, buttons: button === 2 ? 2 : 1,
    bubbles: true, cancelable: true,
  }));
}

it.each(["shapes", "drawnShapes"] as const)("BUI-16 %s labels are literal, including markup, executable attributes and entity-like text", async route => {
  const text = '<tspan id="injected" onmouseover="globalThis.executed = true">&lt; & \" Plan</tspan>';
  const { host, controller } = renderer({ ...base, [route]: [{ orig: "e2", label: { text } }] });
  await frame();
  expect(host.querySelector("#injected, [onmouseover]")).toBeNull();
  expect(host.querySelector(".cg-custom-svgs text")!.textContent).toBe(text);
  // Replay actual emitted output in a disposable script-enabled DOM, as in the
  // original reproduction. No native browser execution is claimed.
  const replay = new JSDOM(host.innerHTML, { runScripts: "dangerously" });
  try {
    replay.window.document.querySelector("text")!.dispatchEvent(new replay.window.MouseEvent("mouseover", { bubbles: true }));
    expect((replay.window as any).executed).toBeUndefined();
    expect(replay.window.document.querySelector("text")!.textContent).toBe(text);
  } finally { replay.window.close(); }
  controller.clearInput(); await frame();
  expect(host.querySelector("text")!.textContent).toBe(text);
});

it.each(["shapes", "drawnShapes"] as const)("BUI-17 %s discards undeclared nested fields and detaches caller/render/event snapshots", async route => {
  const source = { orig: "e2" as const, brush: "red" as const, dest: "e4" as const,
    customSvg: { html: '<circle id="injected-svg" />' },
    label: { text: "&lt;Plan>", fill: "#f00", onclick: "bad" },
    piece: { role: "pawn" as const, color: "white" as const, scale: 0.8, html: "bad" },
    modifiers: { lineWidth: 4, hilite: "#fff", customSvg: { html: "bad" } } };
  const received = vi.fn();
  const { api, controller, host } = renderer({ ...base, [route]: [source], onDrawingChange: received });
  const stored = route === "shapes" ? api.state.drawable.autoShapes : api.state.drawable.shapes;
  expect(stored[0]).not.toHaveProperty("customSvg");
  expect(stored[0].label).not.toHaveProperty("onclick");
  expect(stored[0].piece).not.toHaveProperty("html");
  expect(stored[0].modifiers).not.toHaveProperty("customSvg");
  source.label.text = "caller mutation"; source.label.fill = "#00f"; source.piece.scale = 2;
  source.modifiers.lineWidth = 20; source.customSvg.html = '<circle id="mutated-svg" />';
  expect(stored[0].label!.text).toBe("&amp;lt;Plan&gt;");
  expect(stored[0].piece!.scale).toBe(0.8); expect(stored[0].modifiers!.lineWidth).toBe(4);
  // A drawing event can contain both existing supplied labels and a new gesture.
  api.state.drawable.onChange!([...stored, { orig: "a1", customSvg: { html: "bad" } }]);
  const output = received.mock.calls[0][0].shapes;
  expect(output[0].label.text).toBe("&lt;Plan>"); expect(output[1]).not.toHaveProperty("customSvg");
  output[0].label.text = "event mutation"; output[0].piece.color = "black"; output[0].modifiers.lineWidth = 100;
  expect(stored[0].label!.text).toBe("&amp;lt;Plan&gt;"); expect(stored[0].piece!.color).toBe("white");
  // Explicit cancellation restores the committed snapshot, not caller mutations.
  controller.clearInput(); await frame();
  expect(host.querySelector("#injected-svg, #mutated-svg")).toBeNull();
  const restored = route === "shapes" ? api.state.drawable.autoShapes : api.state.drawable.shapes;
  expect(restored[0].label!.text).toBe("&amp;lt;Plan&gt;");
  stored[0].label!.text = "renderer mutation";
  controller.update({ ...base, [route]: [{ orig: "e2", label: { text: "new" } }] }); await frame();
  expect(host.querySelector("text")!.textContent).toBe("new");
});

it.each(["shapes", "drawnShapes"] as const)("BUI-18 %s reconciles label fill/text/removal/plane and preserves a held piece", async route => {
  const shape: BoardShape = { orig: "e2", label: { text: "Aa", fill: "#ff0000" } };
  const { api, host, controller } = renderer({ ...base, [route]: [shape] }); await frame();
  hold(host, api); const held = api.state.draggable.current; expect(held).toBeDefined();
  const cancel = vi.spyOn(api, "cancelMove"); const set = vi.spyOn(api, "set"); const redrawAll = vi.spyOn(api, "redrawAll");
  controller.update({ ...base, [route]: [{ ...shape, label: { text: "Aa", fill: "#0000ff" } }] }); await frame();
  expect(host.querySelector(".cg-custom-svgs circle")!.getAttribute("fill")).toBe("#0000ff");
  // Aa and BB collide in the dependency's rolling text hash.
  controller.update({ ...base, [route]: [{ ...shape, label: { text: "BB" } }] }); await frame();
  expect(host.querySelector(".cg-custom-svgs text")!.textContent).toBe("BB");
  controller.update({ ...base, [route]: [{ ...shape, label: { text: "BB" }, below: true }] }); await frame();
  expect(host.querySelector(".cg-custom-below text")!.textContent).toBe("BB");
  expect(host.querySelector(".cg-custom-below circle")!.getAttribute("fill")).toBe("#666");
  expect(host.querySelector(".cg-custom-svgs text")).toBeNull();
  controller.update({ ...base, [route]: [] }); await frame(); expect(host.querySelector("text")).toBeNull();
  expect(api.state.draggable.current).toBe(held); expect(cancel).not.toHaveBeenCalled();
  expect(set).not.toHaveBeenCalled(); expect(redrawAll).not.toHaveBeenCalled();
});

it.each([0, 2])("BUI-19 destruction during held input (button %s) stops frames, callbacks and DOM mutation", async button => {
  const handler = vi.fn(); const { api, host, controller } = renderer({ ...base, onMove: handler, onSelect: handler, onDrawingChange: handler });
  await frame(); hold(host, api, button);
  expect(button ? api.state.drawable.current : api.state.draggable.current).toBeDefined();
  controller.destroy();
  expect(api.state.draggable.current).toBeUndefined(); expect(api.state.drawable.current).toBeUndefined();
  expect(api.state.dom.destroyed).toBe(true);
  const html = host.innerHTML; const frames = vi.spyOn(globalThis, "requestAnimationFrame");
  const mutations = vi.fn(); const observer = new MutationObserver(mutations); observer.observe(host, { subtree: true, attributes: true, childList: true });
  await vi.advanceTimersByTimeAsync(200);
  document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 100, clientY: 100 }));
  document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true })); await frame();
  observer.disconnect(); expect(mutations).not.toHaveBeenCalled(); expect(host.innerHTML).toBe(html);
  expect(frames).not.toHaveBeenCalled(); expect(handler).not.toHaveBeenCalled();
});

it("BUI-20 pending animation/redraw/deferred move cannot mutate a destroyed or replacement instance", async () => {
  const handler = vi.fn(); const { api, host, controller } = renderer({ ...base, onMove: handler }); await frame();
  api.state.animation.enabled = true;
  controller.selectSquare("e2"); controller.selectSquare("e4");
  expect(api.state.animation.current).toBeDefined();
  controller.destroy();
  const obsolete = host.innerHTML;
  const next = new BoardController(); const replacement = createRenderer(host, next.initialConfiguration()); next.attach(replacement); next.update({ ...base, positionIdentity: "replacement" });
  cleanup = () => { next.destroy(); host.remove(); };
  await frame(); const current = host.innerHTML;
  const frames = vi.spyOn(globalThis, "requestAnimationFrame");
  api.state.dom.redraw(); api.state.dom.redrawNow(); api.redrawAll();
  await vi.advanceTimersByTimeAsync(250);
  expect(handler).not.toHaveBeenCalled(); expect(frames).not.toHaveBeenCalled(); expect(host.innerHTML).toBe(current);
  expect(api.state.dom.elements.board.outerHTML).toBe(new DOMParser().parseFromString(obsolete, "text/html").querySelector("cg-board")!.outerHTML);
});

it("BUI-23 real drawing completion emits detached literal labels and accepts its snapshot without double encoding", async () => {
  const text = '<tspan onload="bad">&lt;Plan></tspan>';
  const handler = vi.fn();
  const { api, controller, host } = renderer({ ...base,
    drawnShapes: [{ orig: "a1", label: { text } }], onDrawingChange: handler });
  await frame(); hold(host, api, 2); await frame();
  document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 2 }));
  expect(handler).toHaveBeenCalledOnce();
  const event = handler.mock.calls[0][0]; expect(event.shapes).toHaveLength(2);
  expect(event.shapes[0].label.text).toBe(text);
  controller.update({ ...base, drawnShapes: event.shapes, onDrawingChange: handler });
  event.shapes[0].label.text = "consumer mutation";
  await frame(); expect(host.querySelector("text")!.textContent).toBe(text);
  expect(host.querySelector("tspan, [onload]")).toBeNull();
});

it("BUI-24 obsolete DOM generation redraws and input listeners cannot affect the current orientation", async () => {
  const handler = vi.fn(); const { api, host, controller } = renderer({ ...base, onDrawingChange: handler });
  await frame();
  const old = api.state.dom; controller.update({ ...base, orientation: "black", onDrawingChange: handler });
  await frame(); const html = host.innerHTML;
  const frames = vi.spyOn(globalThis, "requestAnimationFrame");
  old.redraw(); old.redrawNow();
  old.elements.board.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 2, clientX: 100, clientY: 100 }));
  await frame(); expect(frames).not.toHaveBeenCalled(); expect(api.state.drawable.current).toBeUndefined();
  expect(host.innerHTML).toBe(html); expect(handler).not.toHaveBeenCalled();
});

it.each([{}, { locked: true }, { readOnly: true }, { mode: "select" as const }, { drawingEnabled: false }])(
  "BUI-21 initially disabled features survive permission/mode/orientation transitions (%j)", async patch => {
    const { api, host, controller } = renderer({ ...base, ...patch }); await frame();
    const contextmenu = () => {
      const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2 });
      host.querySelector("cg-board")!.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
    };
    expect(api.state.dom.elements.ghost).toBeDefined(); contextmenu();
    controller.update({ ...base, ...patch, orientation: "black" }); await frame();
    expect(api.state.dom.elements.ghost).toBeDefined(); contextmenu();
    controller.update(base); await frame();
    hold(host, api); expect(api.state.draggable.current).toBeDefined();
    expect(api.state.dom.elements.ghost!.style.visibility).toBe("");
    api.cancelMove(); hold(host, api, 2); expect(api.state.drawable.current).toBeDefined(); contextmenu();
  });

it("BUI-22 Strict Mode, mode/orientation changes and retained observer callbacks leave no live resources", async () => {
  vi.useFakeTimers(); vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const observers: { callback: () => void; element?: Element; disconnect: ReturnType<typeof vi.fn> }[] = [];
  vi.stubGlobal("ResizeObserver", class {
    record: typeof observers[number];
    constructor(callback: () => void) { this.record = { callback, disconnect: vi.fn() }; observers.push(this.record); }
    observe(element: Element) { this.record.element = element; }
    disconnect() { this.record.disconnect(); }
  });
  const host = document.createElement("div"); document.body.append(host);
  const anchor = document.createElement("div"); const root = createRoot(host);
  cleanup = () => { act(() => root.unmount()); host.remove(); };
  act(() => root.render(<StrictMode><Board {...base} layoutAnchor={anchor} /></StrictMode>));
  const renderers = () => observers.filter(item => item.element?.classList.contains("cg-wrap"));
  expect(renderers()).toHaveLength(2); expect(observers).toHaveLength(6);
  for (const mode of ["free", "select", "legal"] as const)
    act(() => root.render(<StrictMode><Board {...base} mode={mode} layoutAnchor={anchor} /></StrictMode>));
  for (const orientation of ["black", "white", "black"] as const)
    act(() => root.render(<StrictMode><Board {...base} orientation={orientation} layoutAnchor={anchor} /></StrictMode>));
  expect(renderers()).toHaveLength(2); expect(observers).toHaveLength(6);
  await act(() => frame());
  const board = host.querySelector("cg-board")!;
  const bounds = vi.spyOn(board, "getBoundingClientRect");
  bounds.mockClear();
  for (const record of observers.filter(item => item.disconnect.mock.calls.length)) act(() => record.callback());
  expect(bounds).not.toHaveBeenCalled();
  act(() => renderers().at(-1)!.callback()); expect(bounds).toHaveBeenCalled();
  cleanup(); cleanup = () => {};
  for (const observer of observers) expect(observer.disconnect).toHaveBeenCalledOnce();
  const frames = vi.spyOn(globalThis, "requestAnimationFrame");
  for (const observer of observers) act(() => observer.callback());
  await act(() => vi.advanceTimersByTimeAsync(200)); expect(frames).not.toHaveBeenCalled();
});
