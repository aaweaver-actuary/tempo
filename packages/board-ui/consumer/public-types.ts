import type { BoardAppearance, BoardInteractionSnapshot, BoardRendererEvent, BoardColor, BoardHandle, BoardMode, BoardProps, BoardShape, BoardTheme, DrawingEvent,
  InteractionEvent, MoveEvent, PieceRole, PieceSet, PositionIdentity, SelectEvent, Square } from "@tempo/board-ui";

export interface PublicTypes {
  appearance: BoardAppearance; diagnostic: BoardInteractionSnapshot; rendererEvent: BoardRendererEvent;
  color: BoardColor; handle: BoardHandle; mode: BoardMode; props: BoardProps; shape: BoardShape;
  theme: BoardTheme; drawing: DrawingEvent; interaction: InteractionEvent; move: MoveEvent;
  role: PieceRole; pieceSet: PieceSet; identity: PositionIdentity; select: SelectEvent; square: Square;
}

// @ts-expect-error internal controllers must not be importable by a consumer
import type { BoardController } from "@tempo/board-ui/dist/controller.js";
// @ts-expect-error input ownership must be explicit
const incomplete: BoardProps = { fen: "8/8/8/8/8/8/8/8 w - - 0 1" };
void incomplete;
