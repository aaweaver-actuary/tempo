import { Chess } from "chess.js";
import type { TablebaseResult } from "../types";

/** Retained callers receive an explicit local failure; no provider fallback. */
export async function probeTablebase(_fen: string): Promise<TablebaseResult> {
  throw new Error("On-demand endgame calculation has been removed");
}

export function tablebaseCategoryForWhite(fen: string, category: string): "win" | "draw" | "loss" {
  const normalized = ["cursed-win", "blessed-loss"].includes(category) ? "draw" : category.includes("win")
    ? "win" : category.includes("loss") ? "loss" : "draw";
  if (new Chess(fen).turn() === "w") return normalized;
  return normalized === "win" ? "loss" : normalized === "loss" ? "win" : "draw";
}
