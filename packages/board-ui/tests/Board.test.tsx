import { StrictMode, act, createRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { Board } from "../src/Board.js";
import type { BoardHandle, BoardProps } from "../src/types.js";
import { DEFAULT_BOARD_APPEARANCE, resolveBoardAppearance } from "../src/appearance.js";

const factory = vi.hoisted(() => ({ instances: [] as any[] }));
vi.mock("../src/renderer.js", () => ({ createRenderer: vi.fn((_surface, config) => {
  const instance = { initial: config, state: { drawable: {}, dom: { bounds: { clear: vi.fn() }, redraw: vi.fn() } },
    set: vi.fn(), destroy: vi.fn(), cancelMove: vi.fn(), redrawAll: vi.fn(),
    setShapes: vi.fn(), setAutoShapes: vi.fn(), selectSquare: vi.fn() };
  factory.instances.push(instance); return instance;
}) }));

const props: BoardProps = { fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1", positionIdentity: "first",
  positionRevision: 0, orientation: "white", turn: "white", check: false, mode: "legal" };
let root: Root | undefined;
let host: HTMLDivElement;
function mount(element: React.ReactNode) {
  host = document.createElement("div"); document.body.append(host);
  root = createRoot(host); act(() => root!.render(element));
}
afterEach(() => {
  act(() => root?.unmount()); root = undefined; host?.remove(); factory.instances.length = 0;
});

it.each((["brown", "blue", "green"] as const).flatMap(theme =>
  (["cburnett", "merida"] as const).map(pieceSet => ({ theme, pieceSet }))))(
  "BUI-26 shared appearance renders $theme/$pieceSet without replacing the board",
  appearance => {
    mount(<Board {...props} />);
    const api = factory.instances[0];
    const surface = host.querySelector(".tempo-board-surface");
    api.cancelMove.mockClear(); api.set.mockClear();
    act(() => root!.render(<Board {...props} {...appearance} />));
    expect(host.querySelector(".tempo-board-surface")).toBe(surface);
    expect(surface?.classList.contains(`tempo-theme-${appearance.theme}`)).toBe(true);
    expect(surface?.classList.contains(`tempo-pieces-${appearance.pieceSet}`)).toBe(true);
    expect(factory.instances).toHaveLength(1);
    expect(api.cancelMove).not.toHaveBeenCalled(); expect(api.set).not.toHaveBeenCalled();
  },
);

it("BUI-27 invalid appearance values use the canonical defaults or an explicit saved fallback", () => {
  expect(resolveBoardAppearance({ theme: "invalid", pieceSet: null })).toEqual(DEFAULT_BOARD_APPEARANCE);
  expect(resolveBoardAppearance({ theme: "green", pieceSet: "invalid" }, { theme: "blue", pieceSet: "merida" }))
    .toEqual({ theme: "green", pieceSet: "merida" });
});

it("BUI-28 renderer lifecycle facts report genuine resets and leave appearance-only input intact", () => {
  const onRendererEvent = vi.fn();
  mount(<Board {...props} onRendererEvent={onRendererEvent} />);
  expect(onRendererEvent).toHaveBeenCalledWith({ type: "ready", changed: [] });
  onRendererEvent.mockClear();
  act(() => root!.render(<Board {...props} theme="blue" pieceSet="merida" onRendererEvent={onRendererEvent} />));
  expect(onRendererEvent).not.toHaveBeenCalled();
  act(() => root!.render(<Board {...props} positionRevision={1} onRendererEvent={onRendererEvent} />));
  expect(onRendererEvent).toHaveBeenCalledWith({ type: "reset", changed: ["positionRevision"] });
});

it("BUI-09 Strict Mode constructs clean lifecycles, fences previous callbacks and destroys each instance once", () => {
  const onMove = vi.fn();
  mount(<StrictMode><Board {...props} onMove={onMove} /></StrictMode>);
  expect(factory.instances).toHaveLength(2);
  const [first, second] = factory.instances;
  expect(first.destroy).toHaveBeenCalledOnce(); expect(second.destroy).not.toHaveBeenCalled();
  const firstEvents = first.set.mock.calls[0][0];
  firstEvents.movable.events.after("e2", "e4"); expect(onMove).not.toHaveBeenCalled();
  const currentEvents = second.set.mock.calls[0][0];
  act(() => root!.render(<StrictMode><Board {...props} onMove={onMove} theme="green" /></StrictMode>));
  currentEvents.movable.events.after("e2", "e4"); expect(onMove).toHaveBeenCalledOnce();
  act(() => root!.unmount()); root = undefined;
  expect(second.destroy).toHaveBeenCalledOnce(); currentEvents.movable.events.after("e2", "e4");
  expect(onMove).toHaveBeenCalledOnce();
  expect(host.querySelector(".cg-wrap")).toBeNull();
});

it("BUI-10 accessible focus and keyboard seams stay available while read-only blocks imperative input", () => {
  const handle = createRef<BoardHandle>(); const onInteraction = vi.fn(); const onKeyDown = vi.fn();
  mount(<Board {...props} ref={handle} ariaLabel="Analysis position" ariaDescribedBy="instructions"
    onInteraction={onInteraction} onKeyDown={onKeyDown} />);
  act(() => handle.current!.focus());
  const board = host.querySelector('[role="group"]')!;
  expect(document.activeElement).toBe(board);
  expect(board.getAttribute("aria-label")).toBe("Analysis position");
  expect(board.getAttribute("aria-describedby")).toBe("instructions");
  board.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  expect(onKeyDown).toHaveBeenCalledOnce();
  expect(onInteraction).toHaveBeenCalledExactlyOnceWith({ type: "focus", positionIdentity: "first", positionRevision: 0 });
  handle.current!.selectSquare("e2"); expect(factory.instances[0].selectSquare).toHaveBeenCalledExactlyOnceWith("e2");
  act(() => root!.render(<Board {...props} ref={handle} readOnly onInteraction={onInteraction} />));
  expect(document.activeElement).toBe(board); expect(board.getAttribute("aria-disabled")).toBe("true");
  handle.current!.selectSquare("e4"); expect(factory.instances[0].selectSquare).toHaveBeenCalledOnce();
  act(() => (board as HTMLElement).blur());
  expect(onInteraction).toHaveBeenLastCalledWith({ type: "blur", positionIdentity: "first", positionRevision: 0 });
});

it("BUI-11 heading translation and input capture clear bounds without reconstructing or cancelling a held board", () => {
  let headingResize: (() => void) | undefined;
  const disconnects: ReturnType<typeof vi.fn>[] = [];
  const heading = document.createElement("div");
  vi.stubGlobal("ResizeObserver", class {
    disconnect = vi.fn();
    constructor(private notify: () => void) { disconnects.push(this.disconnect); }
    observe(element: HTMLElement) { if (element === heading) headingResize = this.notify; }
  });
  mount(<Board {...props} layoutAnchor={heading} />);
  const api = factory.instances[0];
  api.cancelMove.mockClear(); api.set.mockClear(); api.state.dom.bounds.clear.mockClear();
  act(() => headingResize!());
  host.querySelector(".cg-wrap")!.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));
  expect(api.state.dom.bounds.clear).toHaveBeenCalledTimes(3);
  expect(api.cancelMove).not.toHaveBeenCalled(); expect(api.set).not.toHaveBeenCalled();
  expect(api.redrawAll).not.toHaveBeenCalled(); expect(factory.instances).toHaveLength(1);
  act(() => root!.unmount()); root = undefined;
  for (const disconnect of disconnects) expect(disconnect).toHaveBeenCalledOnce();
  document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true }));
  expect(api.state.dom.bounds.clear).toHaveBeenCalledTimes(3);
});
