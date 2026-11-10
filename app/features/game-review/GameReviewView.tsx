"use client";

import { useEffect, useState } from "react";
import { Board } from "@tempo/board-ui";
import { Chess } from "chess.js";
import "@tempo/board-ui/styles.css";
import { LEARNING_URL as API_URL, learningFetch } from "../../lib/study-connection";

type SavedGame = { id: string; provider: string; played_at: string; color: string; result: string };
type Comparison = { kind: string; reason?: string; ply?: number; played_uci?: string; expected_uci?: string[]; actor?: string };
type Detail = { positions: string[]; moves: string[]; comparison: Comparison; color?: string };

export function GameReviewView() {
  const [games, setGames] = useState<SavedGame[]>([]);
  const [repertoires, setRepertoires] = useState<{ id: string; name: string }[]>([]);
  const [repertoire, setRepertoire] = useState("");
  const [pgn, setPgn] = useState("");
  const [color, setColor] = useState("white");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [ply, setPly] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    Promise.all([
      learningFetch(`${API_URL}/api/product/game-review/games`, { signal: abort.signal }).then(response => response.json()),
      learningFetch(`${API_URL}/api/repertoires`, { signal: abort.signal }).then(response => response.json()),
    ]).then(([saved, authored]) => {
      setGames(saved.games ?? []);
      const items = Array.isArray(authored) ? authored : authored.repertoires ?? [];
      setRepertoires(items); setRepertoire(items[0]?.id ?? "");
    }).catch(error => { if (!abort.signal.aborted) setError(String(error)); });
    return () => abort.abort();
  }, []);
  async function load(game?: SavedGame) {
    setBusy(true); setError("");
    try {
      const base = `${API_URL}/api/product/game-review`;
      const response = await learningFetch(game ? `${base}/games/${encodeURIComponent(game.id)}?repertoire_id=${encodeURIComponent(repertoire)}` : `${base}/compare`, game ? {} : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pgn, color, repertoire_id: repertoire }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Review failed (${response.status})`);
      setDetail(data); setPly(Math.max(0, (data.comparison.ply ?? 1) - 1));
      if (game) setColor(data.color === "black" ? "black" : "white");
    } catch (error) { setError(String(error)); } finally { setBusy(false); }
  }
  const position = detail ? new Chess(detail.positions[ply]) : null;
  return <section aria-label="Game Review" className="p-4 space-y-4">
    <h2>Game Review</h2>
    <label>Repertoire <select value={repertoire} onChange={event => { setRepertoire(event.target.value); setDetail(null); }}>
      {repertoires.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>
    <label>Your color <select value={color} onChange={event => setColor(event.target.value)}><option value="white">White</option><option value="black">Black</option></select></label>
    <label className="block">Paste PGN<textarea className="block w-full" rows={7} value={pgn} onChange={event => setPgn(event.target.value)} /></label>
    <button disabled={busy || !repertoire || !pgn.trim()} onClick={() => void load()}>Review PGN</button>
    {error && <p role="alert">{error}</p>}
    {detail && <div>
      <div style={{ maxWidth: 540 }}><Board fen={detail.positions[ply]} positionIdentity={detail.positions[ply]} positionRevision={ply} orientation={color === "black" ? "black" : "white"} turn={position?.turn() === "b" ? "black" : "white"} check={position?.isCheck() ? (position.turn() === "b" ? "black" : "white") : false} mode="select" readOnly /></div>
      <button disabled={ply === 0} onClick={() => setPly(value => value - 1)}>Previous</button>
      <span> Move {ply} / {detail.moves.length} </span>
      <button disabled={ply === detail.moves.length} onClick={() => setPly(value => value + 1)}>Next</button>
      <p>{detail.comparison.kind === "departure" ? `First departure at ply ${detail.comparison.ply}: ${detail.comparison.played_uci}; repertoire: ${detail.comparison.expected_uci?.join(", ")}.` : detail.comparison.reason ?? detail.comparison.kind.replaceAll("_", " ")}</p>
    </div>}
    <h3>Saved games</h3>
    <ul>{games.map(game => <li key={game.id}><button disabled={busy || !repertoire} onClick={() => void load(game)}>{game.provider} · {game.played_at} · {game.result}</button></li>)}</ul>
  </section>;
}
