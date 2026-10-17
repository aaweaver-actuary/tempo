import type { Api } from "@lichess-org/chessground/api";
import type { Config } from "@lichess-org/chessground/config";
import type { DrawShape } from "@lichess-org/chessground/draw";
import { cancel as cancelDrawing } from "@lichess-org/chessground/draw";
import type { BoardProps, BoardShape, Square } from "./types.js";
import { copyDestinations, copyShapes, sameDestinations, sameShapes } from "./position.js";

const brushes = {
  green: { key: "g", color: "#4f8a59", opacity: 0.88, lineWidth: 10 },
  red: { key: "r", color: "#b45f50", opacity: 0.88, lineWidth: 10 },
  blue: { key: "b", color: "#4e7ca8", opacity: 0.88, lineWidth: 10 },
  yellow: { key: "y", color: "#d0a83f", opacity: 0.92, lineWidth: 11 },
  maia: { key: "m", color: "#8a62a5", opacity: 0.9, lineWidth: 10 },
};

function isSquare(value: string): value is Square { return /^[a-h][1-8]$/.test(value); }

/** Internal adapter. A fresh instance owns each mounted Chessground lifecycle. */
export class BoardController {
  private api?: Api;
  private props?: BoardProps;
  private generation = 0;
  private active = true;
  private autoShapes: BoardShape[] = [];
  private drawnShapes: BoardShape[] = [];

  attach(api: Api): void { this.api = api; }

  private canInteract(): boolean {
    return this.active && Boolean(this.props) && !this.props?.locked && !this.props?.readOnly;
  }

  private events(): Config {
    const generation = this.generation;
    const current = () => generation === this.generation && this.canInteract() ? this.props : undefined;
    return {
      movable: { events: { after: (from, to) => {
        const props = current();
        if (!props || props.mode === "select" || !isSquare(from) || !isSquare(to)) return;
        const event = { from, to, positionIdentity: props.positionIdentity, positionRevision: props.positionRevision };
        if (props.mode === "free") props.onFreeMove?.(event);
        else props.onMove?.(event);
      } } },
      events: { select: square => {
        const props = current();
        if (props && props.mode !== "legal" && isSquare(square)) props.onSelect?.({ square,
          positionIdentity: props.positionIdentity, positionRevision: props.positionRevision });
      } },
      drawable: { onChange: (shapes: DrawShape[]) => {
        const props = current();
        if (props && props.drawingEnabled !== false) props.onDrawingChange?.({
          shapes: copyShapes(shapes as BoardShape[]), positionIdentity: props.positionIdentity,
          positionRevision: props.positionRevision,
        });
      } },
    };
  }

  update(next: BoardProps, forceReset = false): void {
    if (!this.active || !this.api) return;
    const previous = this.props;
    const modeChanged = previous !== undefined && previous.mode !== next.mode;
    const invalidated = forceReset || !previous || previous.fen !== next.fen ||
      previous.positionIdentity !== next.positionIdentity || previous.positionRevision !== next.positionRevision ||
      previous.orientation !== next.orientation || previous.turn !== next.turn || modeChanged ||
      Boolean(previous.locked) !== Boolean(next.locked) || Boolean(previous.readOnly) !== Boolean(next.readOnly) ||
      (previous.drawingEnabled !== false) !== (next.drawingEnabled !== false) ||
      !sameDestinations(previous.destinations, next.destinations);
    // Snapshot all mutable input data. Future callback-only updates use the latest committed handlers.
    this.props = { ...next, destinations: copyDestinations(next.destinations),
      lastMove: next.lastMove ? [...next.lastMove] : undefined };
    const config: Config = {};
    if (invalidated) {
      this.generation += 1;
      this.api.cancelMove();
      // cancelMove only cancels piece input in Chessground 10.1.1. A held drawing
      // otherwise finishes through the replacement onChange handler.
      cancelDrawing(this.api.state);
      // Chessground moves pieces before its delayed callback; cancellation alone cannot restore them.
      Object.assign(config, { fen: next.fen, orientation: next.orientation, turnColor: next.turn,
        check: next.check, lastMove: next.lastMove ? [...next.lastMove] : undefined });
      const enabled = this.canInteract();
      Object.assign(config, this.events());
      config.movable = { ...config.movable, free: next.mode === "free",
        color: !enabled || next.mode === "select" ? undefined : next.mode === "free" ? "both" : next.turn,
        dests: copyDestinations(next.destinations), showDests: next.showDestinations !== false };
      config.draggable = { enabled: enabled && next.mode !== "select", showGhost: true };
      config.selectable = { enabled };
      config.drawable = { ...config.drawable, enabled: enabled && next.drawingEnabled !== false };
    } else {
      if (previous.check !== next.check) config.check = next.check;
      if (previous.lastMove?.[0] !== next.lastMove?.[0] || previous.lastMove?.[1] !== next.lastMove?.[1])
        config.lastMove = next.lastMove ? [...next.lastMove] : undefined;
      if (previous.showDestinations !== next.showDestinations)
        config.movable = { showDests: next.showDestinations !== false };
    }
    if (modeChanged) this.api.redrawAll();
    if (Object.keys(config).length) this.api.set(config);
    const autoShapes = next.shapes ?? [];
    const drawnShapes = next.drawnShapes ?? [];
    if (invalidated || !sameShapes(this.autoShapes, autoShapes)) {
      this.autoShapes = copyShapes(autoShapes);
      this.api.setAutoShapes(copyShapes(autoShapes) as DrawShape[]);
    }
    if (invalidated || !sameShapes(this.drawnShapes, drawnShapes)) {
      this.drawnShapes = copyShapes(drawnShapes);
      this.api.setShapes(copyShapes(drawnShapes) as DrawShape[]);
    }
  }

  initialConfiguration(): Config {
    return { viewOnly: false, coordinates: true, coordinatesOnSquares: true,
      animation: { enabled: !globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches, duration: 180 },
      premovable: { enabled: false }, predroppable: { enabled: false }, autoCastle: true,
      drawable: { brushes, visible: true, enabled: false },
      movable: { color: undefined }, draggable: { enabled: false }, selectable: { enabled: false },
    };
  }

  clearInput(): void { if (this.props) this.update(this.props, true); }
  clearInputBounds(): void { this.api?.state.dom.bounds.clear(); }
  selectSquare(square: Square | null): void { if (this.canInteract()) this.api?.selectSquare(square); }
  destroy(): void {
    if (!this.active) return;
    this.active = false;
    this.generation += 1;
    if (this.api) cancelDrawing(this.api.state);
    this.api?.destroy();
    this.api = undefined;
    this.props = undefined;
  }
}
