import type { BoardProps, BoardShape, Square } from "./types.js";

export function copyShapes(shapes: readonly BoardShape[] = []): BoardShape[] {
  // TypeScript structural compatibility is not a runtime allowlist. In particular,
  // Chessground also accepts customSvg, which is deliberately outside our contract.
  return shapes.map(shape => ({ orig: shape.orig, dest: shape.dest, brush: shape.brush, below: shape.below,
    ...(shape.modifiers ? { modifiers: { lineWidth: shape.modifiers.lineWidth, hilite: shape.modifiers.hilite } } : {}),
    ...(shape.piece ? { piece: { role: shape.piece.role, color: shape.piece.color, scale: shape.piece.scale } } : {}),
    ...(shape.label ? { label: { text: shape.label.text, fill: shape.label.fill } } : {}),
  }));
}

/** Chessground 10.1.1 uses innerHTML for label text. Encode only at its boundary. */
export function rendererShapes(shapes: readonly BoardShape[]): BoardShape[] {
  return copyShapes(shapes).map(shape => shape.label ? { ...shape,
    label: { ...shape.label, text: shape.label.text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") },
  } : shape);
}

/** Inverse of our encoding for renderer-owned drawing snapshots, never HTML parsing. */
export function drawingShapes(shapes: readonly BoardShape[]): BoardShape[] {
  return copyShapes(shapes).map(shape => shape.label ? { ...shape,
    label: { ...shape.label, text: shape.label.text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&") },
  } : shape);
}

export function sameShapes(left: readonly BoardShape[], right: readonly BoardShape[]): boolean {
  return left.length === right.length && left.every((shape, index) => {
    const other = right[index];
    return shape.orig === other.orig && shape.dest === other.dest && shape.brush === other.brush &&
      shape.below === other.below && shape.modifiers?.lineWidth === other.modifiers?.lineWidth &&
      shape.modifiers?.hilite === other.modifiers?.hilite && shape.piece?.role === other.piece?.role &&
      shape.piece?.color === other.piece?.color && shape.piece?.scale === other.piece?.scale &&
      shape.label?.text === other.label?.text && shape.label?.fill === other.label?.fill;
  });
}

export function copyDestinations(destinations: BoardProps["destinations"]): Map<Square, Square[]> {
  return new Map([...destinations ?? []].map(([origin, targets]) => [origin, [...new Set(targets)].sort()]));
}

export function sameDestinations(left: BoardProps["destinations"], right: BoardProps["destinations"]): boolean {
  const first = copyDestinations(left);
  const second = copyDestinations(right);
  return first.size === second.size && [...first].every(([origin, targets]) => {
    const other = second.get(origin);
    return other?.length === targets.length && targets.every((target, index) => target === other[index]);
  });
}

export function fitBoardSize(availableWidth: number, maxSize = 760): number {
  if (!Number.isFinite(availableWidth) || !Number.isFinite(maxSize)) return 0;
  return Math.max(0, Math.floor(Math.min(availableWidth, maxSize) / 8) * 8);
}
