from __future__ import annotations
from pydantic import BaseModel
from typing import Literal, Annotated
from .square import ChessboardSquare


class UciMove(BaseModel):
    from_square: ChessboardSquare
    to_square: ChessboardSquare
    promotes_to: Annotated[Literal["q", "r", "b", "n"], "Promotion piece"] | None = None

    @classmethod
    def from_str(cls, move: str) -> UciMove:
        from_square = ChessboardSquare.from_str(move[:2])
        to_square = ChessboardSquare.from_str(move[2:4])
        promotes_to = move[4] if len(move) == 5 else None
        return cls(
            from_square=from_square,
            to_square=to_square,
            promotes_to=promotes_to,  # ty: ignore[invalid-argument-type]
        )
