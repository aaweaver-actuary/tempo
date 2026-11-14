from __future__ import annotations
from pydantic import BaseModel
from typing import Literal, Annotated


class ChessboardFile(BaseModel):
    value: Annotated[Literal["a", "b", "c", "d", "e", "f", "g", "h"], "File value"]


class ChessboardRank(BaseModel):
    value: Annotated[Literal["1", "2", "3", "4", "5", "6", "7", "8"], "Rank value"]


class ChessboardSquare(BaseModel):
    file: ChessboardFile
    rank: ChessboardRank

    @classmethod
    def from_str(cls, square: str) -> ChessboardSquare:
        square = square.lower()
        file_str = square[0]
        rank_str = square[1]
        if file_str not in "abcdefgh" or rank_str not in "12345678":
            raise ValueError("Invalid square string")
        file = ChessboardFile(value=file_str)  # ty: ignore[invalid-argument-type]
        rank = ChessboardRank(value=rank_str)  # ty: ignore[invalid-argument-type]
        return cls(file=file, rank=rank)
