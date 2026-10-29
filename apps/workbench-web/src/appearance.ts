import { DEFAULT_BOARD_APPEARANCE, resolveBoardAppearance, type BoardAppearance } from "@tempo/board-ui/appearance";

type AppearanceStorage = Pick<Storage, "getItem" | "setItem">;

/** Launch values > this origin's last launch > shared defaults. No service dependency. */
export function readWorkbenchAppearance(url: URL, storage?: AppearanceStorage): BoardAppearance {
  let saved = DEFAULT_BOARD_APPEARANCE;
  try {
    const browserStorage = storage ?? globalThis.localStorage;
    saved = resolveBoardAppearance({ theme: browserStorage.getItem("tempo-board-theme"), pieceSet: browserStorage.getItem("tempo-piece-set") });
  } catch { /* Appearance remains usable when browser storage is unavailable. */ }
  const appearance = resolveBoardAppearance({ theme: url.searchParams.get("boardTheme"), pieceSet: url.searchParams.get("pieceSet") }, saved);
  try {
    const browserStorage = storage ?? globalThis.localStorage;
    browserStorage.setItem("tempo-board-theme", appearance.theme);
    browserStorage.setItem("tempo-piece-set", appearance.pieceSet);
  } catch { /* The launch snapshot still applies for this document. */ }
  return appearance;
}
