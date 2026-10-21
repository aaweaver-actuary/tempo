"""Shared engine attempt diagnostic contract."""
from typing import Literal
from pydantic import BaseModel, Field


class EngineAttemptDiagnostics(BaseModel):
    model_config = {"extra": "forbid", "strict": True}
    outcome: Literal["success", "preempted", "timeout", "failure"]
    elapsed_seconds: float = Field(ge=0, le=3600, allow_inf_nan=False)
