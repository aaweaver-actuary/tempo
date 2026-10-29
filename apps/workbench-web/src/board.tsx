import { Board, type BoardAppearance, type BoardColor, type BoardProps } from "@tempo/board-ui";
import { Chess } from "chess.js";
import { createRoot } from "react-dom/client";

interface DisplayPosition {
  readonly fen: string;
  readonly orientation: BoardColor;
}

/** The DOM application owns navigation; this island owns only the shared Board mount. */
export function mountBoard(target: HTMLElement, initial: DisplayPosition & {
  readonly identity: string; readonly appearance: BoardAppearance; readonly label: string;
}) {
  const root = createRoot(target);
  let current: DisplayPosition = initial;
  let revision = 0;
  let destroyed = false;
  function render() {
    const chess = new Chess(current.fen);
    const turn = chess.turn() === "w" ? "white" : "black";
    const props: BoardProps = { ...current, ...initial.appearance,
      positionIdentity: initial.identity, positionRevision: revision,
      turn, check: chess.isCheck() ? turn : false, mode: "select", readOnly: true,
      drawingEnabled: false, ariaLabel: initial.label, maxSize: 760,
    };
    root.render(<Board {...props} />);
  }
  render();
  return {
    update(next: Partial<DisplayPosition>) {
      if (destroyed) return;
      if (next.fen !== undefined && next.fen !== current.fen || next.orientation !== undefined && next.orientation !== current.orientation) revision++;
      current = { ...current, ...next };
      render();
    },
    destroy() { if (!destroyed) { destroyed = true; root.unmount(); } },
  };
}
