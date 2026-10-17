"use client";

import { Chessground } from "@lichess-org/chessground";
import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { BoardController } from "./controller.js";
import { fitBoardSize } from "./position.js";
import type { BoardHandle, BoardProps } from "./types.js";

export const Board = forwardRef<BoardHandle, BoardProps>(function Board(props, ref) {
  const hostRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const controllerRef = useRef<BoardController | null>(null);
  const [size, setSize] = useState(0);

  useImperativeHandle(ref, () => ({
    focus: () => hostRef.current?.focus(),
    clearInput: () => controllerRef.current?.clearInput(),
    clearInputBounds: () => controllerRef.current?.clearInputBounds(),
    selectSquare: square => controllerRef.current?.selectSquare(square),
  }), []);

  useLayoutEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const controller = new BoardController();
    controller.attach(Chessground(surface, controller.initialConfiguration()));
    controllerRef.current = controller;
    const refresh = () => controller.clearInputBounds();
    surface.addEventListener("mousedown", refresh, true);
    surface.addEventListener("touchstart", refresh, { capture: true, passive: true });
    // A banner may translate the board during a held gesture without resizing it.
    document.addEventListener("mousemove", refresh, true);
    document.addEventListener("touchmove", refresh, { capture: true, passive: true });
    return () => {
      surface.removeEventListener("mousedown", refresh, true);
      surface.removeEventListener("touchstart", refresh, true);
      document.removeEventListener("mousemove", refresh, true);
      document.removeEventListener("touchmove", refresh, true);
      controller.destroy();
      controllerRef.current = null;
      surface.replaceChildren();
    };
  }, []);

  useLayoutEffect(() => { controllerRef.current?.update(props); });

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const measure = () => {
      setSize(fitBoardSize(host.getBoundingClientRect().width, props.maxSize));
      controllerRef.current?.clearInputBounds();
    };
    const observer = typeof ResizeObserver === "undefined" ? undefined : new ResizeObserver(measure);
    observer?.observe(host);
    window.addEventListener("resize", measure);
    window.visualViewport?.addEventListener("resize", measure);
    measure();
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
      window.visualViewport?.removeEventListener("resize", measure);
    };
  }, [props.maxSize]);

  useLayoutEffect(() => {
    if (!props.layoutAnchor || typeof ResizeObserver === "undefined") return;
    const refresh = () => controllerRef.current?.clearInputBounds();
    const observer = new ResizeObserver(refresh);
    observer.observe(props.layoutAnchor);
    refresh();
    return () => observer.disconnect();
  }, [props.layoutAnchor]);

  return <div ref={hostRef} role="group" aria-label={props.ariaLabel ?? "Interactive chessboard"}
    aria-describedby={props.ariaDescribedBy} aria-disabled={Boolean(props.locked || props.readOnly)}
    tabIndex={props.tabIndex ?? 0} onKeyDown={props.onKeyDown}
    onFocus={event => { if (!event.currentTarget.contains(event.relatedTarget)) props.onInteraction?.({ type: "focus",
      positionIdentity: props.positionIdentity, positionRevision: props.positionRevision }); }}
    onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) props.onInteraction?.({ type: "blur",
      positionIdentity: props.positionIdentity, positionRevision: props.positionRevision }); }}
    className={`tempo-board ${props.className ?? ""}`} style={props.style}
    data-position-identity={props.positionIdentity} data-position-revision={props.positionRevision}
    data-fen={props.fen} data-orientation={props.orientation} data-mode={props.mode}>
    <div className={`tempo-board-surface tempo-theme-${props.theme ?? "brown"} tempo-pieces-${props.pieceSet ?? "cburnett"}`}
      style={{ width: size, height: size, visibility: size ? "visible" : "hidden" }}>
      <div className="cg-wrap" ref={surfaceRef} />
    </div>
  </div>;
});
