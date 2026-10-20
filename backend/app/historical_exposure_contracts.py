"""Explicit one-line practice intent; no producer-controlled eligibility facts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExposureSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    target_id: str = Field(min_length=1,max_length=128)
    workspace_id: Literal['legacy']
    repertoire_id: str = Field(min_length=1,max_length=128)
    game_id: str = Field(min_length=1,max_length=256)
    source_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    authority_id: str = Field(min_length=1,max_length=256)
    ply: int = Field(ge=0,le=999)
    card_id: str = Field(min_length=1,max_length=128)
    card_revision: int = Field(ge=1)
    executable_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    route_ids: list[str] = Field(min_length=1,max_length=64)
    expected_moves: list[str] = Field(min_length=1,max_length=16)
    confirmation: Literal['practice_this_encountered_line']
    accepts_unknown_chronology: bool = False

    @model_validator(mode='after')
    def canonical_selection(self):
        if (self.route_ids != sorted(set(self.route_ids))
                or any(not value or len(value)>128 for value in self.route_ids)
                or self.expected_moves != sorted(set(self.expected_moves))
                or any(len(value) not in (4,5) for value in self.expected_moves)):
            raise ValueError('Explicit canonical route/move selection required')
        return self
