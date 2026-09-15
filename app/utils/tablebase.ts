import { Chess } from "chess.js";
import type { TablebaseResult } from "../types";

export async function probeTablebase(fen: string): Promise<TablebaseResult> {
  const local =
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1"].includes(location.hostname);
  const url = local
    ? `${process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"}/api/endgames/probe`
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
  const normalized = category.includes("win")
    ? "win"
    : category.includes("loss")
      ? "loss"
      : "draw";
  if (new Chess(fen).turn() === "w") return normalized;
  return normalized === "win" ? "loss" : normalized === "loss" ? "win" : "draw";
}
