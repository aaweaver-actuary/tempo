"""Executable definitions: shape validation, never chess engine execution."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import Model, Identifier, Revision, UciMove, Square


class MoveLine(Model):
    type: Literal["move_line"]
    accepted_lines: list[
        Annotated[list[UciMove], Field(min_length=1, max_length=40)]
    ] = Field(min_length=1, max_length=20)
    mode: Literal["single", "stepwise_line"]
    grading_policy: Literal["reference", "open_judgment"]


class SquareSet(Model):
    type: Literal["square_set"]
    required: list[Square] = Field(max_length=64)
    optional: list[Square] = Field(max_length=64)
    criterion: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def distinct_squares(self):
        if len(set(self.required + self.optional)) != len(
            self.required + self.optional
        ):
            raise ValueError("square sets must be distinct and disjoint")
        return self


class KnightPath(Model):
    type: Literal["knight_path"]
    start_square: Square
    target_squares: list[Square] = Field(min_length=1, max_length=8)
    minimum_hops: Annotated[int, Field(ge=0, le=3)]
    maximum_hops: Annotated[int, Field(ge=0, le=3)]
    hop_rule: Literal["at_most", "exact"]
    occupancy_rule: Literal["static_non_capturing"]

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.minimum_hops > self.maximum_hops:
            raise ValueError("minimum_hops exceeds maximum_hops")
        return self


class ChoiceOption(Model):
    id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=1000)]


class Choice(Model):
    type: Literal["choice"]
    options: list[ChoiceOption] = Field(min_length=2, max_length=12)
    correct_option_ids: list[Identifier] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def known_choices(self):
        option_ids = [option.id for option in self.options]
        if (
            len(set(option_ids)) != len(option_ids)
            or len(set(self.correct_option_ids)) != len(self.correct_option_ids)
            or not set(self.correct_option_ids) <= set(option_ids)
        ):
            raise ValueError("choices must have distinct IDs and known correct options")
        return self


class Explanation(Model):
    type: Literal["explanation"]
    rubric: Annotated[str, Field(min_length=1, max_length=10000)]


Specification = Annotated[
    MoveLine | SquareSet | KnightPath | Choice | Explanation,
    Field(discriminator="type"),
]


class ExecutableExercise(Model):
    card_id: Identifier
    decision_id: Identifier
    policy_id: Identifier
    source_revision: Revision
    publication_id: Identifier
    publication_generation: Revision
    exercise_revision: Revision
    starting_fen: Annotated[str, Field(min_length=1, max_length=120)]
    prompt: Annotated[str, Field(min_length=1, max_length=2000)]
    specification: Specification
