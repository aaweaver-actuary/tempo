import type { CSSProperties, KeyboardEventHandler } from "react";

export type BoardColor = "white" | "black";
export type Square = `${"a" | "b" | "c" | "d" | "e" | "f" | "g" | "h"}${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8}`;
export type BoardMode = "legal" | "free" | "select";
export type BoardTheme = "brown" | "blue" | "green";
export type PieceSet = "cburnett" | "merida";
export type PieceRole = "pawn" | "knight" | "bishop" | "rook" | "queen" | "king";

export interface PositionIdentity {
  readonly positionIdentity: string;
  readonly positionRevision: number;
}

/** Plain display data. No executable SVG, application stores, or legality rules. */
export interface BoardShape {
  readonly orig: Square;
  readonly dest?: Square;
  readonly brush?: "green" | "red" | "blue" | "yellow" | "maia";
  readonly modifiers?: { readonly lineWidth?: number; readonly hilite?: string };
  readonly piece?: { readonly role: PieceRole; readonly color: BoardColor; readonly scale?: number };
  readonly label?: { readonly text: string; readonly fill?: string };
  readonly below?: boolean;
}

export interface MoveEvent extends PositionIdentity {
  readonly from: Square;
  readonly to: Square;
}
export interface SelectEvent extends PositionIdentity { readonly square: Square }
export interface DrawingEvent extends PositionIdentity { readonly shapes: readonly BoardShape[] }
export interface InteractionEvent extends PositionIdentity { readonly type: "focus" | "blur" }

export interface BoardProps extends PositionIdentity {
  readonly fen: string;
  readonly orientation: BoardColor;
  readonly turn: BoardColor;
  readonly check: BoardColor | false;
  readonly mode: BoardMode;
  readonly locked?: boolean;
  readonly readOnly?: boolean;
  /** Supplied by the caller. Equivalent maps do not invalidate held input. */
  readonly destinations?: ReadonlyMap<Square, readonly Square[]>;
  readonly showDestinations?: boolean;
  readonly drawingEnabled?: boolean;
  readonly theme?: BoardTheme;
  readonly pieceSet?: PieceSet;
  readonly lastMove?: readonly [Square, Square];
  readonly shapes?: readonly BoardShape[];
  readonly drawnShapes?: readonly BoardShape[];
  readonly maxSize?: number;
  /** Optional heading/palette whose resize can translate the board. */
  readonly layoutAnchor?: HTMLElement | null;
  readonly ariaLabel?: string;
  readonly ariaDescribedBy?: string;
  readonly tabIndex?: number;
  readonly className?: string;
  readonly style?: CSSProperties;
  /** Application keyboard bindings stay with the caller. */
  readonly onKeyDown?: KeyboardEventHandler<HTMLDivElement>;
  readonly onMove?: (event: MoveEvent) => void;
  readonly onFreeMove?: (event: MoveEvent) => void;
  readonly onSelect?: (event: SelectEvent) => void;
  readonly onDrawingChange?: (event: DrawingEvent) => void;
  readonly onInteraction?: (event: InteractionEvent) => void;
}

export interface BoardHandle {
  focus(): void;
  /** Cancel pending input, fence deferred events, and restore the supplied FEN. */
  clearInput(): void;
  /** Refresh hit testing without replacing pieces or interrupting a drag. */
  clearInputBounds(): void;
  /** Keyboard seam; follows the same mode, lock, and supplied destinations. */
  selectSquare(square: Square | null): void;
}
