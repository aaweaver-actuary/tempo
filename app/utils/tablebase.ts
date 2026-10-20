import type { TablebaseResult } from "../types";

/** Compatibility failure for retained callers; calculation and fallback removed. */
export async function probeTablebase(_fen: string): Promise<TablebaseResult> {
  throw new Error("On-demand endgame calculation has been removed");
}
