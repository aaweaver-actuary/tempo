import { Chess } from "chess.js";

export const STANDARD_FEN = new Chess().fen();

declare const __TEMPO_API_URL__: string;
const configuredApiUrl = typeof __TEMPO_API_URL__ !== "undefined" ? __TEMPO_API_URL__ :
  typeof process !== "undefined" ? process.env.NEXT_PUBLIC_API_URL : undefined;
export const API_URL = configuredApiUrl ?? "http://127.0.0.1:8000";

export function assetUrl(path: string): string {
  const normalized = path.replace(/^\/+/, "");
  if (typeof document === "undefined") return `/${normalized}`;
  return new URL(normalized, document.baseURI).href;
}
export const pieceSymbols: Record<string, string> = {
  K: "♔",
  Q: "♕",
  R: "♖",
  B: "♗",
  N: "♘",
  P: "♙",
  k: "♚",
  q: "♛",
  r: "♜",
  b: "♝",
  n: "♞",
  p: "♟",
  "": "×",
};
