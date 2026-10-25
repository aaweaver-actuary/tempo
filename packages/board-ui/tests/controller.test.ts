import { describe, expect, it, vi } from "vitest";
import type { Api } from "@lichess-org/chessground/api";
import type { Config } from "@lichess-org/chessground/config";
import { BoardController } from "../src/controller.js";
import { copyShapes, fitBoardSize } from "../src/position.js";
import type { BoardProps, BoardShape } from "../src/types.js";

const base: BoardProps = { fen: "8/8/8/8/8/8/4P3/4K2k w - - 0 1", positionIdentity: "card-one",
  positionRevision: 0, orientation: "white", turn: "white", check: false, mode: "legal",
  destinations: new Map([["e2", ["e3", "e4"]]]) };

function setup(props = base) {
  const api = { set: vi.fn(), cancelMove: vi.fn(), redrawAll: vi.fn(), setAutoShapes: vi.fn(),
    setShapes: vi.fn(), destroy: vi.fn(), selectSquare: vi.fn(),
    state: { drawable: { current: undefined as unknown }, dom: { bounds: { clear: vi.fn() }, redraw: vi.fn() } } };
  const controller = new BoardController();
  controller.attach(api as unknown as Api);
  controller.update(props);
  const events = () => api.set.mock.calls.findLast(([config]) => config.events)?.[0] as Config;
  const initial = events();
  Object.values(api).forEach(value => { if (vi.isMockFunction(value)) value.mockClear(); });
  return { api, controller, initial, events };
}

function move(config: Config) { config.movable!.events!.after!("e2", "e4", { premove: false }); }
function draw(config: Config) { config.drawable!.onChange!([{ orig: "a1", brush: "red" }]); }

describe("held input and authoritative position", () => {
  it("BUI-01 equivalent props and fresh callbacks preserve held input and forward the latest handler once", () => {
    const oldHandler = vi.fn(); const latestHandler = vi.fn();
    const { controller, api, initial } = setup({ ...base, onMove: oldHandler, lastMove: ["a1", "a2"] });
    for (let index = 0; index < 20; index++) controller.update({ ...base, onMove: latestHandler,
      destinations: new Map([["e2", ["e4", "e3", "e3"]]]), lastMove: ["a1", "a2"] });
    expect(api.cancelMove).not.toHaveBeenCalled(); expect(api.set).not.toHaveBeenCalled();
    move(initial);
    expect(oldHandler).not.toHaveBeenCalled();
    expect(latestHandler).toHaveBeenCalledExactlyOnceWith({ from: "e2", to: "e4", positionIdentity: "card-one", positionRevision: 0 });
  });

  it("BUI-02 annotations, appearance, check and last move never reapply FEN or cancel held input", () => {
    const { controller, api } = setup();
    controller.update({ ...base, theme: "blue", pieceSet: "merida", check: "white", lastMove: ["e2", "e4"],
      shapes: [{ orig: "a1", dest: "h8", label: { text: "Plan" } }], drawnShapes: [{ orig: "b2" }] });
    expect(api.cancelMove).not.toHaveBeenCalled();
    expect(api.set).toHaveBeenCalledExactlyOnceWith({ check: "white", lastMove: ["e2", "e4"] });
    controller.update(base);
    expect(api.set).toHaveBeenLastCalledWith({ check: false, lastMove: undefined });
  });

  it.each([
    ["same-FEN identity replacement", { positionIdentity: "card-two" }],
    ["same-FEN revision replacement", { positionRevision: 1 }],
    ["FEN replacement", { fen: "8/8/8/8/4P3/8/8/4K2k b - - 0 1" }],
    ["free mode", { mode: "free" }], ["select mode", { mode: "select" }],
    ["lock", { locked: true }], ["read-only", { readOnly: true }],
    ["orientation", { orientation: "black" }], ["turn", { turn: "black" }],
    ["changed destinations", { destinations: new Map([["e2" as const, ["e3"] as const]]) }],
    ["drawing lock", { drawingEnabled: false }],
  ] as const)("BUI-03 %s cancels input, restores FEN, and fences stale move/select/drawing callbacks", (_name, patch) => {
    const handler = vi.fn();
    const props: BoardProps = { ...base, onMove: handler, onSelect: handler, onDrawingChange: handler };
    const { controller, api, initial } = setup(props);
    const replacement: BoardProps = { ...props, ...patch };
    controller.update(replacement);
    expect(api.cancelMove).toHaveBeenCalledOnce();
    expect(api.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: replacement.fen }));
    move(initial); draw(initial); initial.events!.select!("a1");
    expect(handler).not.toHaveBeenCalled();
  });

  it("BUI-04 free/select modes route only their typed event, and lock/unlock replaces the input generation", () => {
    const onMove = vi.fn(); const onFreeMove = vi.fn(); const onSelect = vi.fn();
    const props: BoardProps = { ...base, onMove, onFreeMove, onSelect, mode: "free" };
    const { controller, api, initial, events } = setup(props);
    move(initial); initial.events!.select!("a1");
    expect(onFreeMove).toHaveBeenCalledOnce(); expect(onSelect).toHaveBeenCalledOnce(); expect(onMove).not.toHaveBeenCalled();
    controller.update({ ...props, locked: true });
    const locked = events(); move(locked); locked.events!.select!("a2");
    expect(api.set).toHaveBeenLastCalledWith(expect.objectContaining({
      draggable: { enabled: false, showGhost: true }, selectable: { enabled: false }, drawable: expect.objectContaining({ enabled: false }),
    }));
    controller.update({ ...props, mode: "select" });
    move(initial); move(locked); move(events()); events().events!.select!("h8");
    expect(onFreeMove).toHaveBeenCalledOnce(); expect(onSelect).toHaveBeenCalledTimes(2);
    expect(api.set).toHaveBeenLastCalledWith(expect.objectContaining({ draggable: { enabled: false, showGhost: true } }));
  });

  it("BUI-05 obsolete callbacks after explicit cancellation and destruction cannot affect current handlers", () => {
    const handler = vi.fn(); const { controller, api, initial, events } = setup({ ...base, onMove: handler, onDrawingChange: handler });
    controller.clearInput(); move(initial); draw(initial); expect(handler).not.toHaveBeenCalled();
    const current = events(); controller.destroy(); controller.destroy();
    move(current); draw(current); controller.update(base); controller.selectSquare("a1");
    expect(handler).not.toHaveBeenCalled(); expect(api.destroy).toHaveBeenCalledOnce();
  });
});

it("BUI-06 nested shape inputs, outputs, defaults, and destination maps are detached from Chessground mutation", () => {
  const shape: BoardShape = { orig: "e2", dest: "e4", label: { text: "Plan" }, modifiers: { lineWidth: 4 },
    piece: { role: "pawn", color: "white" } };
  const shapes = [shape]; const onDrawingChange = vi.fn();
  const props = { ...base, shapes, drawnShapes: shapes, onDrawingChange };
  const { controller, api, initial } = setup(props);
  // Get a newly applied array without relying on references in the previous snapshot.
  controller.clearInput();
  const imperative = api.setShapes.mock.calls.at(-1)![0];
  imperative[0].label.text = "mutated"; imperative[0].piece.color = "black"; imperative.push({ orig: "b2" });
  const auto = api.setAutoShapes.mock.calls.at(-1)![0]; auto[0].modifiers.lineWidth = 100;
  expect(shapes).toEqual(copyShapes([shape])); expect(shape.label?.text).toBe("Plan");
  expect(shape.piece?.color).toBe("white"); expect(shape.modifiers?.lineWidth).toBe(4);
  const destinations = api.set.mock.calls.at(-1)![0].movable.dests;
  destinations.get("e2").push("a1");
  expect(base.destinations!.get("e2")).toEqual(["e3", "e4"]);
  const latest = api.set.mock.calls.at(-1)![0];
  const input = copyShapes(shapes); latest.drawable.onChange(input);
  onDrawingChange.mock.calls[0][0].shapes[0].label.text = "consumer mutation";
  expect(input[0].label?.text).toBe("Plan");
  // Original callback was invalidated even though drawing arrays have the same values.
  draw(initial); expect(onDrawingChange).toHaveBeenCalledOnce();
  api.setShapes.mockClear(); api.setAutoShapes.mockClear(); controller.update(props);
  expect(api.setShapes).not.toHaveBeenCalled(); expect(api.setAutoShapes).not.toHaveBeenCalled();
});

it("BUI-07 translation clears cached bounds without cancellation; editor transition refreshes geometry without rebuilding DOM", () => {
  const { controller, api } = setup({ ...base, mode: "free" });
  controller.clearInputBounds();
  expect(api.state.dom.bounds.clear).toHaveBeenCalledOnce();
  expect(api.set).not.toHaveBeenCalled(); expect(api.cancelMove).not.toHaveBeenCalled(); expect(api.redrawAll).not.toHaveBeenCalled();
  controller.update(base); expect(api.redrawAll).not.toHaveBeenCalled();
  expect(api.state.dom.bounds.clear).toHaveBeenCalledTimes(2);
});

it("BUI-08 responsive sizes have integral squares and respect bounds including zero-width containers", () => {
  for (const width of [0, 1, 7, 8, 319.5, 390, 643, 768, 1280]) {
    const size = fitBoardSize(width); expect(size % 8).toBe(0); expect(size).toBeLessThanOrEqual(width);
    expect(size).toBeLessThanOrEqual(760);
  }
  expect(fitBoardSize(640, 319)).toBe(312); expect(fitBoardSize(-12)).toBe(0);
  expect(fitBoardSize(Number.NaN)).toBe(0);
});

it("BUI-12 identity replacement cancels a live drawing before installing the replacement handler", () => {
  const onDrawingChange = vi.fn(); const { controller, api } = setup({ ...base, onDrawingChange });
  api.state.drawable.current = { orig: "a1", mouseSq: "h8" };
  controller.update({ ...base, positionIdentity: "replacement", onDrawingChange });
  expect(api.state.drawable.current).toBeUndefined();
  expect(api.state.dom.redraw).toHaveBeenCalledOnce();
  expect(onDrawingChange).not.toHaveBeenCalled();
});
