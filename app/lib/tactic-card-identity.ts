import { canonicalFenKey } from "@tempo/chess-line";

// Python str.split/strip whitespace, including C0 separators and NEL; unlike
// ECMAScript whitespace it excludes U+FEFF. Persisted IDs use Python's rules.
const pythonWhitespace = /[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/gu;
const pythonTrim = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;

/** Match the tactic producer's Python str.split() when reading a frozen line. */
export function splitTacticMoves(moves: string): string[] {
  return moves.split(pythonWhitespace).filter(Boolean);
}

/** Match the persisted Study card identity for a frozen tactic's training line. */
export async function tacticCardIdentity(fen: string, uciMoves: readonly string[]): Promise<string> {
  const normalized = fen.replace(pythonWhitespace, " ");
  // FEFF is not Python whitespace, but canonicalFenKey's JS trim/split treats
  // it as such; preserve it as part of its field in this exceptional case.
  const key = normalized.includes("\ufeff")
    ? normalized.split(" ").filter(Boolean).slice(0, 4).join(" ")
    : canonicalFenKey(normalized);
  const payload = `${key}\n${uciMoves.map(move => move.replace(pythonTrim, "")).join(" ")}`;
  if (!globalThis.crypto?.subtle) throw new Error("Tactic identity verification is unavailable.");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}
