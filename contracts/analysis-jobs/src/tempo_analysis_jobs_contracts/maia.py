"""Maia bounded inference wire v1; no model or provider import."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import Model, WireModel, Identifier, Probability, UciMove, Revision
from ._envelope import JobRequest, ResultProvenance


class MaiaJob(JobRequest):
    worker: Literal["maia"]
    fen: Annotated[str, Field(min_length=1, max_length=120)]
    rating: Annotated[int, Field(ge=0, le=4000)]
    model_id: Identifier
    model_version: Identifier
    legal_moves: list[UciMove] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def distinct_legal_moves(self):
        if len(set(self.legal_moves)) != len(self.legal_moves):
            raise ValueError("duplicate legal move")
        return self


class PredictedMove(Model):
    move: UciMove
    probability: Probability


class MaiaResult(WireModel):
    workspace_id: Identifier
    job_id: Identifier
    request_revision: Revision
    execution_generation: Revision
    worker: Literal["maia"]
    provenance: ResultProvenance
    model_id: Identifier
    model_version: Identifier
    predictions: list[PredictedMove] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def normalized_predictions(self):
        if (
            len({item.move for item in self.predictions}) != len(self.predictions)
            or abs(sum(item.probability for item in self.predictions) - 1) > 1e-9
        ):
            raise ValueError("predictions must be distinct and sum to one")
        return self
