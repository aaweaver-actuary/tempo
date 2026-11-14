"""Validated, versioned contracts for authored study exercises."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExerciseText(StrictModel):
    prompt: str = Field(min_length=1, max_length=2000)
    hint: str = Field(default="", max_length=2000)
    explanation: str = Field(default="", max_length=10000)
    further_analysis: str = Field(default="", max_length=10000)


class MoveExercise(ExerciseText):
    type: Literal["move_line"]
    grading_policy: Literal["reference", "open_judgment"] = "reference"
    mode: Literal["single", "stepwise_line"] = "single"
    accepted_lines: list[list[str]] = Field(min_length=1, max_length=20)


class SquareExercise(ExerciseText):
    type: Literal["square_set"]
    required: list[str] = Field(default_factory=list, max_length=64)
    optional: list[str] = Field(default_factory=list, max_length=64)
    candidate_region: list[str] | None = None
    criterion: str = Field(min_length=1, max_length=2000)


class KnightExercise(ExerciseText):
    type: Literal["knight_path"]
    start_square: str
    target_squares: list[str] = Field(min_length=1, max_length=8)
    minimum_hops: int = Field(default=1, ge=0, le=3)
    maximum_hops: int = Field(default=2, ge=0, le=3)
    hop_rule: Literal["at_most", "exact"] = "at_most"
    occupancy_rule: Literal["static_non_capturing"] = "static_non_capturing"


class ChoiceOption(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)


class ChoiceExercise(ExerciseText):
    type: Literal["choice"]
    options: list[ChoiceOption] = Field(min_length=2, max_length=12)
    correct_option_ids: list[str] = Field(min_length=1)


class ExplanationExercise(ExerciseText):
    type: Literal["explanation"]
    rubric: str = Field(min_length=1, max_length=10000)


ExerciseSpecification = Annotated[
    MoveExercise
    | SquareExercise
    | KnightExercise
    | ChoiceExercise
    | ExplanationExercise,
    Field(discriminator="type"),
]


class MoveAnswer(StrictModel):
    type: Literal["move_line"]
    moves: list[str] = Field(min_length=1, max_length=40)


class SquareAnswer(StrictModel):
    type: Literal["square_set"]
    squares: list[str] = Field(max_length=64)


class KnightAnswer(StrictModel):
    type: Literal["knight_path"]
    reachable: bool
    path: list[str] = Field(default_factory=list, max_length=4)


class ChoiceAnswer(StrictModel):
    type: Literal["choice"]
    option_ids: list[str] = Field(min_length=1, max_length=12)
    displayed_order: list[str] = Field(default_factory=list, max_length=12)


class ExplanationAnswer(StrictModel):
    type: Literal["explanation"]
    text: str = Field(default="", max_length=10000)
    ready: bool = True


ExerciseAnswer = Annotated[
    MoveAnswer | SquareAnswer | KnightAnswer | ChoiceAnswer | ExplanationAnswer,
    Field(discriminator="type"),
]
