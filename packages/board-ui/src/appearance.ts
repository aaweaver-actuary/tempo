import type { BoardAppearance } from "./types.js";

export const DEFAULT_BOARD_APPEARANCE: BoardAppearance = Object.freeze({ theme: "brown", pieceSet: "cburnett" });

/** Validate presentation inputs without reading browser or application state. */
export function resolveBoardAppearance(
  value: { readonly theme?: unknown; readonly pieceSet?: unknown },
  fallback: BoardAppearance = DEFAULT_BOARD_APPEARANCE,
): BoardAppearance {
  return {
    theme: value.theme === "brown" || value.theme === "blue" || value.theme === "green" ? value.theme : fallback.theme,
    pieceSet: value.pieceSet === "cburnett" || value.pieceSet === "merida" ? value.pieceSet : fallback.pieceSet,
  };
}

export type { BoardAppearance, BoardTheme, PieceSet } from "./types.js";
