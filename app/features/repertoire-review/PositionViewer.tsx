import { useState } from "react";
import { Chess } from "chess.js";
import { Board } from "@tempo/board-ui/interfaces";
import "../../../packages/board-ui/styles/board.css";
import { Button } from "../../components/buttons/BaseButton";
import { positionsFromMoves } from "../../hooks/use-board-history";
import type { ReviewPosition } from "./api";

export function PositionViewer({ position, identity }: { position: ReviewPosition; identity: string }) {
  return <PositionHistory key={`${identity}/${position.target_fen}/${position.history_uci.join(",")}`} position={position} identity={identity} />;
}

function PositionHistory({ position, identity }: { position: ReviewPosition; identity: string }) {
  let positions: readonly string[] = [];
  let error: string | null = null;
  try {
    positions = positionsFromMoves(position.starting_fen, position.history_uci);
    if (positions.length !== position.history_uci.length + 1 || new Chess(positions.at(-1)).fen() !== new Chess(position.target_fen).fen()) {
      error = "Supplied history does not reach the target. Refresh this finding.";
    }
    if (position.variant !== "standard") error = "This board adapter requires a standard-chess position.";
  } catch { error = "Position or history unavailable. Refresh this finding."; }
  const [cursor, setCursor] = useState(position.history_uci.length);
  if (error) return <p role="alert">{error}</p>;
  const fen = positions[cursor];
  const chess = new Chess(fen);
  const turn = chess.turn() === "w" ? "white" : "black";
  return <section className="repertoire-review-board" aria-label="Position and history">
    <Board fen={fen} orientation={position.learner_color} turn={turn}
      check={chess.isCheck() ? turn : false} mode="select" readOnly drawingEnabled={false}
      positionIdentity={`${identity}/${cursor}`} positionRevision={cursor}
      ariaLabel={cursor === positions.length - 1 ? "Repertoire target position" : `History position ${cursor}`} maxSize={560} />
    <div className="repertoire-review-actions" aria-label="Move history controls">
      <Button disabled={cursor === 0} onClick={() => setCursor(value => value - 1)}>Previous position</Button>
      <span aria-live="polite">{cursor} / {position.history_uci.length}</span>
      <Button disabled={cursor === positions.length - 1} onClick={() => setCursor(value => value + 1)}>Next position</Button>
      <Button disabled={cursor === positions.length - 1} onClick={() => setCursor(positions.length - 1)}>Target position</Button>
    </div>
    <p>Target: <code>{position.target_fen}</code></p>
    <p>History: {position.history_uci.join(" ") || "Starting position"}</p>
    <small>{position.variant} · learner {position.learner_color} · supplied history only</small>
  </section>;
}
