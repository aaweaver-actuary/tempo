// Package-owned lifecycle adapter for pinned Chessground 10.1.1. Its top-level
// factory does not retain ResizeObservers or cancel its deferred redraw. Keep
// upstream rules/rendering/input modules; own construction and disposal here.
// Derived from lichess-org/chessground (GPL-3.0-or-later); see notices/THIRD-PARTY.md.
import { start, type Api } from "@lichess-org/chessground/api";
import { configure, type Config } from "@lichess-org/chessground/config";
import { defaults, type State } from "@lichess-org/chessground/state";
import { renderWrap } from "@lichess-org/chessground/wrap";
import { render, renderResized, updateBounds } from "@lichess-org/chessground/render";
import { render as renderAutoPieces, renderResized as resizeAutoPieces } from "@lichess-org/chessground/autoPieces";
import { renderSvg } from "@lichess-org/chessground/svg";
import { bindDocument } from "@lichess-org/chessground/events";
import * as drag from "@lichess-org/chessground/drag";
import * as draw from "@lichess-org/chessground/draw";
import { memo, isRightButton, createEl, setVisible } from "@lichess-org/chessground/util";
import type { MouchEvent } from "@lichess-org/chessground/types";

export function createRenderer(element: HTMLElement, config: Config): Api {
  const state = defaults() as State;
  configure(state, config);
  let alive = true;
  let frame: number | undefined;
  let unbindBoard = () => {};
  let unbindDocument = () => {};
  const resize = () => {
    if (!alive) return;
    state.dom.bounds.clear();
    updateBounds(state);
    renderResized(state);
    if (state.dom.elements.autoPieces) resizeAutoPieces(state);
  };
  // The wrap is stable across orientation redraws. One observer owns its entire
  // lifetime and always reads the current DOM; retained notifications are fenced.
  const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(resize);
  const redrawAll = () => {
    if (!alive) return;
    if (frame !== undefined) cancelAnimationFrame(frame);
    frame = undefined;
    unbindBoard();
    const elements = renderWrap(element, state);
    // Permissions may start disabled. Ghost structure must still exist before
    // later set() calls enable dragging, including after orientation changes.
    if (!elements.ghost) {
      elements.ghost = createEl("piece", "ghost");
      setVisible(elements.ghost, false);
      elements.container.appendChild(elements.ghost);
    }
    const redrawNow = (skipSvg?: boolean) => {
      if (!alive || state.dom.elements !== elements) return;
      render(state);
      if (elements.autoPieces) renderAutoPieces(state, elements.autoPieces);
      if (!skipSvg && elements.shapes) {
        renderSvg(state, elements);
        // Encoding changes the renderer's string length. Use the literal length
        // for the pinned label's typography while retaining its geometry.
        for (const text of element.querySelectorAll(".cg-custom-svgs text, .cg-custom-below text")) {
          const scale = 0.75 ** (text.textContent?.length ?? 0);
          text.setAttribute("font-size", String(0.4 * scale));
          text.setAttribute("y", String(0.13 * scale));
        }
      }
    };
    state.dom = { elements, bounds: memo(() => elements.board.getBoundingClientRect()), redrawNow,
      redraw: () => {
        if (!alive || state.dom.elements !== elements || frame !== undefined) return;
        frame = requestAnimationFrame(() => { frame = undefined; redrawNow(); });
      }, unbind: () => { unbindBoard(); unbindDocument(); observer?.disconnect(); } };
    state.drawable.prevSvgHash = "";
    updateBounds(state);
    redrawNow();
    // Only package-supported piece/drawing input is bound. The upstream document
    // binder returns a disposer; its board binder's observer is unowned.
    const onStart = (event: Event) => {
      if (!alive || state.dom.elements !== elements) return;
      const input = event as MouchEvent;
      if (state.draggable.current) drag.cancel(state);
      else if (state.drawable.current) draw.cancel(state);
      else if (input.shiftKey || isRightButton(input)) {
        if (state.drawable.enabled) draw.start(state, input);
      } else if (!state.viewOnly) drag.start(state, input);
    };
    const onContextMenu = (event: Event) => { if (alive) event.preventDefault(); };
    elements.board.addEventListener("contextmenu", onContextMenu);
    elements.board.addEventListener("mousedown", onStart, { passive: false });
    elements.board.addEventListener("touchstart", onStart, { passive: false });
    unbindBoard = () => {
      elements.board.removeEventListener("contextmenu", onContextMenu);
      elements.board.removeEventListener("mousedown", onStart);
      elements.board.removeEventListener("touchstart", onStart);
    };
  };
  redrawAll();
  unbindDocument = bindDocument(state, resize);
  observer?.observe(element);
  const api = start(state, redrawAll);
  const invalidateShapes = () => {
    // Upstream hashes omit label.fill and use a lossy text hash. Rebuild only
    // annotation children on explicit shape updates; pieces/held input survive.
    state.drawable.prevSvgHash = "";
    for (const layer of [state.dom.elements.shapes, state.dom.elements.shapesBelow,
      state.dom.elements.custom, state.dom.elements.customBelow]) layer?.querySelector("g")?.replaceChildren();
  };
  return { ...api,
    setShapes: shapes => { if (alive) { invalidateShapes(); api.setShapes(shapes); } },
    setAutoShapes: shapes => { if (alive) { invalidateShapes(); api.setAutoShapes(shapes); } },
    destroy: () => {
      if (!alive) return;
      api.cancelMove();
      draw.cancel(state);
      alive = false;
      if (frame !== undefined) cancelAnimationFrame(frame);
      frame = undefined;
      api.destroy();
    },
  };
}
