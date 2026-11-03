import type { PracticeCard } from "../cards";
import {
  decodePositionPresentation,
  type PositionPresentation,
} from "../position-presentation";
import type { PieceColor } from "../shared";

export type PositionPresentationDisplay = Readonly<{
  plan: PositionPresentation;
  learnerColor: PieceColor;
  orientation: PieceColor;
  opponentCueMoves: readonly string[];
}>;

/**
 * Bind the immutable server plan to the original queue card for display.
 * This never trims or rewrites the legacy card and does not select a route.
 */
export function adaptPositionPresentation(
  raw: unknown,
  originalCard: PracticeCard,
): PositionPresentationDisplay {
  const plan = decodePositionPresentation(raw);
  if (
    originalCard.backendId !== plan.original_card_id ||
    originalCard.queueEntryId !== plan.queue_entry_id ||
    originalCard.revision !== plan.original_card_revision
  ) {
    throw new Error("position_presentation_original_card_mismatch");
  }

  const firstDecision = plan.decision_slots[0];
  const learnerColor = firstDecision.position_key.split(" ")[1];
  if (learnerColor !== "w" && learnerColor !== "b") {
    throw new Error("position_presentation_invalid_decision_color");
  }
  const cueMoves = Object.freeze(plan.moves_uci.slice(0, firstDecision.move_offset));
  const color: PieceColor = learnerColor === "w" ? "white" : "black";
  return Object.freeze({
    plan,
    learnerColor: color,
    orientation: color,
    opponentCueMoves: cueMoves,
  });
}
