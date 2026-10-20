import { useState } from "react";
import { Chess } from "chess.js";
import { Button } from "../components/buttons/BaseButton";
import { BoardTheme, PieceSet, Chessboard } from "../components/chessboard";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import { STANDARD_FEN } from "../const";
import { PracticeCard } from "../types";

export default function EndgamesView({ theme, pieceSet, scheduledCard, onBury,
  blocked = false, useSharedBoard = false,
}: {
  theme: BoardTheme;
  pieceSet: PieceSet;
  onQueueChanged: () => void;
  scheduledCard?: PracticeCard;
  onReview?: (outcome: "correct" | "again") => void;
  onBury?: () => Promise<void>;
  blocked?: boolean;
  useSharedBoard?: boolean;
}) {
  const [burying, setBurying] = useState(false);
  const [buryError, setBuryError] = useState("");
  let savedFen: string | undefined;
  try { if (scheduledCard) { new Chess(scheduledCard.startingFen); savedFen = scheduledCard.startingFen; } }
  catch { /* Invalid stored data stays unavailable; do not fabricate a position. */ }
  const fen = savedFen ?? STANDARD_FEN;
  const notice = scheduledCard
    ? "This saved material template has no playable stored solution. On-demand endgame calculation has been removed. Its saved position and history are retained."
    : "On-demand endgame calculation has been removed. Saved endgame cards with a stored solution remain available in daily training.";
  useBoardPublisher("endgames", useSharedBoard ? {
    positionKey: `saved-endgame:${scheduledCard?.queueEntryId ?? scheduledCard?.id ?? "unavailable"}`,
    fen, interactionMode: "readonly", showHint: false, theme, pieceSet,
    orientation: scheduledCard?.orientation ?? "white",
    unavailable: savedFen ? undefined : notice,
  } : null);

  async function bury() {
    if (!onBury || burying || blocked) return;
    setBurying(true);
    setBuryError("");
    try { await onBury(); }
    catch (error) { setBuryError(`Could not bury this card. ${error instanceof Error ? error.message : "Retry the action."}`); }
    finally { setBurying(false); }
  }

  return <section className={`endgames-page${scheduledCard ? " scheduled-endgame" : ""}`} inert={blocked}>
    <h1>{scheduledCard?.title ?? "Endgames"}</h1>
    <p role="status">{notice}</p>
    {scheduledCard && savedFen && !useSharedBoard && <Chessboard fen={fen} locked showHint={false}
      theme={theme} pieceSet={pieceSet} orientation={scheduledCard.orientation} />}
    {scheduledCard && onBury && <Button disabled={blocked || burying} onClick={() => void bury()}>
      {burying ? "Burying…" : "Bury"}
    </Button>}
    {buryError && <p role="alert">{buryError}</p>}
  </section>;
}
