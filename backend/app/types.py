from pydantic import Field
from typing import Annotated

type DecisionId = Annotated[str, Field(min_length=64, max_length=64)]
type RepertoireId = Annotated[str, Field(min_length=1)]
type MoveId = Annotated[str, Field(min_length=1)]
type GameId = Annotated[str, Field(min_length=1)]

FEN_BOARD_PATTERN = r"[rnbqkpRNBQKP12345678]{8}(/[rnbqkpRNBQKP12345678]{8}){{7}}"
ACTIVE_COLOR_PATTERN = r"[wb]"
CASTLING_PATTERN = r"[KQkq-]"
EN_PASSANT_PATTERN = r"[a-h1-8-]"
HALFMOVE_CLOCK_PATTERN = r"[0-9]+"
FULLMOVE_NUMBER_PATTERN = r"[0-9]+"
type Fen = Annotated[
    str,
    Field(
        min_length=1,
        pattern=f"^{FEN_BOARD_PATTERN} {ACTIVE_COLOR_PATTERN} {CASTLING_PATTERN} {EN_PASSANT_PATTERN} {HALFMOVE_CLOCK_PATTERN} {FULLMOVE_NUMBER_PATTERN}$",
    ),
]
