import type { BoardColor, BoardDiagnosticEvent, BoardHandle, BoardInputSnapshot, BoardMode, BoardProps, BoardShape, BoardTheme, DrawingEvent,
  InteractionEvent, MoveEvent, PieceRole, PieceSet, PositionIdentity, SelectEvent, Square } from "@tempo/board-ui";

export interface PublicTypes {
  color: BoardColor; diagnostic: BoardDiagnosticEvent; snapshot: BoardInputSnapshot; handle: BoardHandle; mode: BoardMode; props: BoardProps; shape: BoardShape;
  theme: BoardTheme; drawing: DrawingEvent; interaction: InteractionEvent; move: MoveEvent;
  role: PieceRole; pieceSet: PieceSet; identity: PositionIdentity; select: SelectEvent; square: Square;
}

// @ts-expect-error internal controllers must not be importable by a consumer
import type { BoardController } from "@tempo/board-ui/dist/controller.js";
// @ts-expect-error input ownership must be explicit
const incomplete: BoardProps = { fen: "8/8/8/8/8/8/8/8 w - - 0 1" };
void incomplete;

function diagnosticsAreReadOnly(snapshot: BoardInputSnapshot) {
  // @ts-expect-error diagnostic snapshots do not expose writable renderer state
  snapshot.bounds.left = 5;
  // @ts-expect-error pointer coordinates are immutable consumer data
  snapshot.originalPointer[0] = 5;
}
void diagnosticsAreReadOnly;
