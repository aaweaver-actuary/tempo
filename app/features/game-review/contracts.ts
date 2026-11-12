import { Chess } from "chess.js";

export type SavedGame = { id: string; provider: string; played_at: string; color: "white" | "black"; result: string };
type Comparison =
  | { kind: "covered" }
  | { kind: "unavailable"; reason: string }
  | { kind: "coverage_end"; ply: number; fen: string }
  | { kind: "departure"; ply: number; fen: string; played_uci: string; expected_uci: string[]; actor: "white" | "black" };
export type Detail = { positions: string[]; moves: string[]; comparison: Comparison; color?: "white" | "black" };

const object = (value: unknown): Record<string, unknown> => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid Game Review response");
  return value as Record<string, unknown>;
};
const string = (value: unknown): string => {
  if (typeof value !== "string" || !value.length) throw new Error("Invalid Game Review response");
  return value;
};
const color = (value: unknown): "white" | "black" => {
  if (value !== "white" && value !== "black") throw new Error("Invalid Game Review color");
  return value;
};
const strings = (value: unknown): string[] => {
  if (!Array.isArray(value)) throw new Error("Invalid Game Review response");
  return value.map(string);
};

export function decodeSavedGames(value: unknown): SavedGame[] {
  const games = object(value).games;
  if (!Array.isArray(games)) throw new Error("Invalid saved games response");
  return games.map(value => { const game = object(value); return { id: string(game.id), provider: string(game.provider), played_at: string(game.played_at), color: color(game.color), result: string(game.result) }; });
}

export function decodeRepertoires(value: unknown): { id: string; name: string }[] {
  const items = Array.isArray(value) ? value : object(value).repertoires;
  if (!Array.isArray(items)) throw new Error("Invalid repertoires response");
  return items.map(value => { const item = object(value); return { id: string(item.id), name: string(item.name) }; });
}

export function decodeGameReview(value: unknown, saved = false): Detail {
  try {
    const data = object(value);
    const positions = strings(data.positions), moves = strings(data.moves);
    if (!positions.length || positions.length !== moves.length + 1) throw new Error();
    const board = new Chess(string(data.starting_fen));
    if (board.fen() !== new Chess(positions[0]).fen()) throw new Error();
    for (let index = 0; index < moves.length; index++) {
      const move = moves[index];
      if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) throw new Error();
      board.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move.slice(4) || undefined });
      if (board.fen() !== new Chess(positions[index + 1]).fen()) throw new Error();
    }
    const raw = object(data.comparison);
    let comparison: Comparison;
    if (raw.kind === "covered") comparison = { kind: "covered" };
    else if (raw.kind === "unavailable") comparison = { kind: "unavailable", reason: string(raw.reason) };
    else if (raw.kind === "coverage_end" || raw.kind === "departure") {
      if (!Number.isInteger(raw.ply) || (raw.ply as number) < 1 || (raw.ply as number) > moves.length) throw new Error();
      const ply = raw.ply as number, fen = string(raw.fen);
      if (new Chess(fen).fen() !== new Chess(positions[ply - 1]).fen()) throw new Error();
      if (raw.kind === "coverage_end") comparison = { kind: raw.kind, ply, fen };
      else {
        const played_uci = string(raw.played_uci), expected_uci = strings(raw.expected_uci), actor = color(raw.actor);
        if (played_uci !== moves[ply - 1] || !expected_uci.length || actor !== (new Chess(fen).turn() === "w" ? "white" : "black")) throw new Error();
        for (const move of expected_uci) {
          if (!/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) throw new Error();
          new Chess(fen).move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move.slice(4) || undefined });
        }
        comparison = { kind: raw.kind, ply, fen, played_uci, expected_uci, actor };
      }
    } else throw new Error();
    if ((raw.kind === "covered" || raw.kind === "unavailable") && raw.ply !== undefined) throw new Error();
    return { positions, moves, comparison, ...(saved || data.color !== undefined ? { color: color(data.color) } : {}) };
  } catch { throw new Error("Invalid Game Review response"); }
}
