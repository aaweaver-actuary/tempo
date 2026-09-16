import { Chess } from "chess.js";
import type { TablebaseResult } from "../types";
import { API_URL } from "../const";
import { usesLocalApi } from "./local";

export async function probeTablebase(fen: string): Promise<TablebaseResult> {
  const local = usesLocalApi();
  const url = local
    ? `${API_URL}/api/endgames/probe`
    : `https://tablebase.lichess.ovh/standard?fen=${encodeURIComponent(fen)}`;
  const response = await fetch(
    url,
    local
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fen }),
        }
      : undefined,
  );
  if (!response.ok) throw new Error("Tablebase unavailable");
  return response.json();
}

export function tablebaseCategoryForWhite(
  fen: string,
  category: string,
): "win" | "draw" | "loss" {
  const normalized = ["cursed-win", "blessed-loss"].includes(category) ? "draw" : category.includes("win")
    ? "win"
    : category.includes("loss")
      ? "loss"
      : "draw";
  if (new Chess(fen).turn() === "w") return normalized;
  return normalized === "win" ? "loss" : normalized === "loss" ? "win" : "draw";
}
