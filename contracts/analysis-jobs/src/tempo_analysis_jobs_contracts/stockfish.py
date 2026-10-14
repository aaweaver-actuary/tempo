"""Stockfish bounded-search wire v1; no engine import or execution."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import Model, WireModel, Identifier, Revision, UciMove
from ._envelope import JobRequest, ResultProvenance


class StockfishJob(JobRequest):
    worker: Literal["stockfish"]
    fen: Annotated[str, Field(min_length=1, max_length=120)]
    moves: list[UciMove] = Field(max_length=1000)
    search_moves: list[UciMove] = Field(max_length=256)
    max_depth: Annotated[int, Field(ge=1, le=128)] | None
    max_nodes: Annotated[int, Field(ge=1, le=2**53 - 1)] | None
    multi_pv: Annotated[int, Field(ge=1, le=256)]
    engine_version: Identifier


class CentipawnScore(Model):
    kind: Literal["centipawns"]
    value: int = Field(ge=-(2**53 - 1), le=2**53 - 1)


class MateScore(Model):
    kind: Literal["mate"]
    value: int = Field(ge=-1000, le=1000)


Score = Annotated[CentipawnScore | MateScore, Field(discriminator="kind")]


class Variation(Model):
    rank: Annotated[int, Field(ge=1, le=256)]
    moves: list[UciMove] = Field(min_length=1, max_length=256)
    score: Score
    score_perspective: Literal["side_to_move"]
    depth: Annotated[int, Field(ge=1, le=128)]


class StockfishResult(WireModel):
    workspace_id: Identifier
    job_id: Identifier
    request_revision: Revision
    execution_generation: Revision
    worker: Literal["stockfish"]
    provenance: ResultProvenance
    engine_version: Identifier
    variations: list[Variation] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_variations(self):
        if [item.rank for item in self.variations] != list(
            range(1, len(self.variations) + 1)
        ):
            raise ValueError("variation ranks must be contiguous")
        return self
